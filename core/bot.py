"""Satu panggilan bot (/api/tick): baca state -> ambil data -> validasi -> sinyal -> eksekusi simulasi -> simpan state + log.

Semua keputusan dicatat. Tidak ada angka yang hanya hidup di memori:
  calls     : setiap panggilan (waktu sebenarnya, status, candle yang diproses, equity di best bid)
  signals   : setiap pasangan x setiap candle yang diproses (indikator, kondisi, aksi)
  orders    : setiap order simulasi (best bid/ask, fill rata-rata, fee, selisih vs harga sinyal)
  equity    : equity harian (panggilan pertama setelah 00.00 WIB)
  errors    : setiap error API/eksepsi
  events    : kill switch, daily loss limit, candle terlewat, data tidak valid
  snapshots : order book top-20 tiap pasangan setiap panggilan (estimasi spread)
"""
import datetime as dt
import json
import os
import secrets
import time
import traceback

from . import strategy as S
from .config import param_hash
from .execution import QTY_DEC, fee_resmi, book_ok, walk_buy_idr, walk_qty, fee_rate, cost_decomposition
from .indodax import drop_unclosed
from .store import LOGS

WIB = dt.timezone(dt.timedelta(hours=7))
SNAP_LEVELS = 20


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wib_date(ts):
    return dt.datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%d")


def init_state(cfg):
    return {"kas": float(cfg["modal_awal"]), "posisi": {}, "last_candle_t": None, "hari": None,
            "equity_awal_hari": None, "dll_hari": None, "kill_terakhir": False, "seq": 0, "last_bid": {}}


def _pair_meta(meta, pair):
    m = (meta or {}).get(pair) or {}
    def f(k):
        try:
            return float(m.get(k))
        except (TypeError, ValueError):
            return None
    return {"fee_api": f("trade_fee_percent_taker"), "min_api": f("trade_min_base_currency"), "qty_dec": QTY_DEC}


def run_tick(store, client, cfg, sumber="cron", clock=time.time):
    p = cfg
    tf = p["timeframe_menit"] * 60
    t0 = clock()
    call_id = f"{int(t0)}-{secrets.token_hex(3)}"
    phash = param_hash(cfg)
    logs = {n: [] for n in LOGS}
    call = {"id": call_id, "mulai_utc": iso(t0), "mulai_ts": round(t0, 3), "sumber": sumber,
            "param_hash": phash, "kode": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "lokal")[:12],
            "status": None, "candle_diproses": None}

    if not store.set("lock", call_id, nx=True, ex=280):
        call["status"] = "dilewati_panggilan_lain_berjalan"
        store.append("calls", [call])
        return call

    state = None
    try:
        raw = store.get("state")
        state = json.loads(raw) if raw else init_state(cfg)
        pos = state["posisi"]

        # ---------------------------------------------------------- kill switch
        kill = (store.get("kill_switch") or "0") == "1"
        call["kill_switch"] = kill
        if kill != state.get("kill_terakhir", False):
            logs["events"].append({"waktu_utc": iso(t0), "call_id": call_id,
                                   "jenis": "kill_switch_ON" if kill else "kill_switch_OFF",
                                   "keterangan": "perubahan flag uas:kill_switch terdeteksi oleh tick"})
            state["kill_terakhir"] = kill

        # ---------------------------------------------------------- waktu & candle target
        srv = client.server_time()
        if srv is None:
            srv = clock()
            call["server_time_fallback"] = True
        call["server_time"] = round(srv, 3)
        target_t = (int(srv) // tf) * tf - tf          # open time candle terakhir yang sudah TERTUTUP
        call["candle_target"] = target_t
        call["candle_target_utc"] = iso(target_t)

        meta = client.pairs()
        if meta:
            state["pairs_meta"] = {k: meta[k] for k in p["pairs"] + [p["pair_rezim"]] if k in meta}
        meta = state.get("pairs_meta", {})

        # ---------------------------------------------------------- data
        L = S.lookback_bars(p)
        semua = list(dict.fromkeys(p["pairs"] + [p["pair_rezim"]]))
        bars, books = {}, {}
        for pair in semua:
            b = client.candles(pair, p["timeframe_menit"], target_t - (L + 10) * tf, int(srv))
            bars[pair] = drop_unclosed(b, p["timeframe_menit"], srv) if b else None
        for pair in semua:
            books[pair] = client.depth(pair)
            bk = books[pair]
            logs["snapshots"].append({"waktu_utc": iso(clock()), "ts": round(clock(), 3), "call_id": call_id, "pair": pair,
                                      "bids": (bk or {}).get("bids", [])[:SNAP_LEVELS],
                                      "asks": (bk or {}).get("asks", [])[:SNAP_LEVELS]})
        binfo = {pair: book_ok(books[pair], p["maks_spread"]) for pair in semua}
        # perataan candle diambil dari data Indodax (bukan diasumsikan kelipatan epoch)
        offs = [bars[x][-1]["t"] % tf for x in semua if bars.get(x)]
        if offs:
            off = max(set(offs), key=offs.count)
            target_t = ((int(srv) - off) // tf) * tf + off - tf
            call["candle_target"] = target_t
            call["candle_target_utc"] = iso(target_t)

        # ---------------------------------------------------------- penilaian equity di best bid
        def mark():
            nilai, basi = 0.0, []
            for pr, ps in pos.items():
                ok, _, info = binfo.get(pr, (False, None, {}))
                bid = info.get("bid") if info.get("bid") else None
                if bid is None and books.get(pr) and books[pr]["bids"]:
                    bid = books[pr]["bids"][0][0]
                if bid is None:
                    bid = state["last_bid"].get(pr, ps["entry_avg"])
                    basi.append(pr)
                state["last_bid"][pr] = bid
                nilai += ps["qty"] * bid
            return state["kas"] + nilai, nilai, basi

        equity, nilai_pos, basi = mark()
        call["equity_awal"] = round(equity, 2)
        if basi:
            call["harga_basi"] = basi

        # ---------------------------------------------------------- hari WIB, equity harian, daily loss limit
        hari = wib_date(srv)
        if state.get("hari") != hari:
            logs["equity"].append({"tanggal_wib": hari, "waktu_utc": iso(srv), "call_id": call_id,
                                   "equity": round(equity, 2), "kas": round(state["kas"], 2),
                                   "nilai_posisi": round(nilai_pos, 2), "n_posisi": len(pos)})
            state["hari"], state["equity_awal_hari"] = hari, equity

        def cek_dll(eq):
            if state.get("dll_hari") != hari and eq <= state["equity_awal_hari"] * (1 - p["daily_loss_limit"]):
                state["dll_hari"] = hari
                logs["events"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "daily_loss_limit",
                                       "keterangan": f"equity {eq:.0f} <= {state['equity_awal_hari']:.0f} x (1-{p['daily_loss_limit']})"})
        cek_dll(equity)

        # ---------------------------------------------------------- idempotency
        last = state.get("last_candle_t")
        if last is not None and target_t <= last:
            call["status"] = "candle_sudah_diproses"
            return _finish(store, state, logs, call, client, clock, equity)
        if not store.set(f"candle:{target_t}", call_id, nx=True):
            call["status"] = "candle_sudah_diproses_panggilan_lain"
            return _finish(store, state, logs, call, client, clock, equity)
        if last is not None:
            for t in range(last + tf, target_t, tf):
                logs["events"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "candle_terlewat",
                                       "candle_t": t, "candle_utc": iso(t)})

        # ---------------------------------------------------------- sinyal (fungsi bersama)
        evals, bars_now = {}, {}
        for pair in semua:
            b = bars[pair]
            if not b:
                evals[pair] = {"valid": False, "alasan": "data_candle_gagal", "entry_raw": False}
                continue
            idx = next((k for k in range(len(b) - 1, -1, -1) if b[k]["t"] == target_t), None)
            if idx is None:
                umur = srv - (b[-1]["t"] + tf)
                evals[pair] = {"valid": False, "entry_raw": False,
                               "alasan": "candle_basi" if umur > p["maks_umur_candle_x_tf"] * tf else "candle_hilang"}
                continue
            bars_now[pair] = b[idx]
            evals[pair] = S.evaluate(b, idx, p)
        rb = bars.get(p["pair_rezim"]) or []
        j = next((k for k in range(len(rb) - 1, -1, -1) if rb[k]["t"] == target_t), None)
        rezim = S.regime_ok(rb, j, p)

        boleh_buka = not kill and state.get("dll_hari") != hari
        ev_plan = {}
        for pair in p["pairs"]:
            ev = evals[pair]
            if ev.get("valid") and not binfo[pair][0]:
                ev = {**ev, "valid": False, "alasan": "orderbook:" + binfo[pair][1]}
            ev_plan[pair] = ev
        held_now = {k: v for k, v in pos.items() if k in bars_now and binfo[k][0]}
        rencana = S.plan(pos, ev_plan, {k: bars_now[k] for k in held_now}, p, boleh_buka, rezim)
        # posisi yang candle-nya ada tetapi order book tidak valid: catat exit tertunda bila sinyal keluar
        for pr in pos:
            if pr in bars_now and not binfo[pr][0]:
                ok_x, why = S.exit_signal(pos[pr], bars_now[pr], p)
                if ok_x:
                    logs["events"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "exit_tertunda",
                                           "pair": pr, "keterangan": f"{why}; {binfo[pr][1]}"})

        aksi = {}
        # ---------------------------------------------------------- eksekusi: keluar dulu
        for pair, why in rencana["exits"]:
            ps = pos[pair]
            info = binfo[pair][2]
            fill = walk_qty(books[pair]["bids"], ps["qty"])
            m = _pair_meta(meta, pair)
            fr = fee_rate(p, m["fee_api"], "sell")
            fee = fill["notional"] * fr
            state["kas"] += fill["notional"] - fee
            sig = bars_now[pair]["c"]
            o = _order(state, call_id, target_t, pair, "sell", fill, info, fee, fr, p, m, sig, why, ps["posisi_id"], clock)
            o["sisa_tidak_terjual"] = round(ps["qty"] - fill["qty"], 12)
            logs["orders"].append(o)
            aksi[pair] = "jual:" + why
            if ps["qty"] - fill["qty"] > 1e-12:
                ps["qty"] -= fill["qty"]           # sisa (kedalaman kurang) tetap dipegang, dicatat
            else:
                del pos[pair]

        equity2, _, _ = mark()
        cek_dll(equity2)
        if state.get("dll_hari") == hari and rencana["entries"]:
            for pr in rencana["entries"]:
                rencana["ditolak"][pr] = "daily_loss_limit"
            rencana["entries"] = []
        if kill and rencana["entries"]:
            rencana["entries"] = []

        # ---------------------------------------------------------- eksekusi: masuk
        for pair in rencana["entries"]:
            info = binfo[pair][2]
            m = _pair_meta(meta, pair)
            fr = fee_rate(p, m["fee_api"], "buy")
            size = S.target_size(equity2, state["kas"], p)
            fill = walk_buy_idr(books[pair]["asks"], size / (1 + fr), m["qty_dec"])
            min_idr = max(p["min_order_idr"], m["min_api"] or 0)
            if fill["qty"] <= 0 or fill["notional"] < min_idr:
                rencana["ditolak"][pair] = f"di_bawah_minimum_{min_idr:.0f}"
                continue
            fee = fill["notional"] * fr
            state["kas"] -= fill["notional"] + fee
            state["seq"] += 1
            pid = f"P{state['seq']:04d}"
            ev = evals[pair]
            o = _order(state, call_id, target_t, pair, "buy", fill, info, fee, fr, p, m, ev["close"], "sinyal_masuk", pid, clock)
            logs["orders"].append(o)
            pos[pair] = {"posisi_id": pid, "qty": fill["qty"], "entry_avg": fill["avg"], "entry_utc": o["waktu_utc"],
                         "sinyal_t": target_t, "sinyal_close": ev["close"], "sinyal_atr": ev["atr"],
                         "biaya_masuk": fee}
            aksi[pair] = "beli"

        # ---------------------------------------------------------- log sinyal per pasangan
        for pair in p["pairs"]:
            ev = evals[pair]
            row = {"candle_t": target_t, "candle_utc": iso(target_t), "pair": pair, "call_id": call_id,
                   "diproses_utc": iso(clock()), "param_hash": phash, "valid": ev.get("valid", False),
                   "alasan_tidak_valid": ev.get("alasan"), "rezim": rezim,
                   "entry_raw": bool(ev.get("entry_raw")), "entry_final": S.entry_final(ev, rezim),
                   "aksi": aksi.get(pair) or ("tolak:" + rencana["ditolak"][pair] if pair in rencana["ditolak"] else "-"),
                   "orderbook": "ok" if binfo[pair][0] else binfo[pair][1]}
            for k in ("close", "level", "sma", "slope", "atr", "atrp", "atrp_mean", "breakout", "di_atas_sma", "slope_ok", "vol_menyempit"):
                if k in ev:
                    row[k] = ev[k]
            exit_flag = None
            if pair in bars_now and (pair in pos or aksi.get(pair, "").startswith("jual")):
                ref = pos.get(pair) or {}
                if ref:
                    exit_flag = S.exit_signal(ref, bars_now[pair], p)[1]
                else:
                    exit_flag = aksi[pair].split(":", 1)[1]
            row["exit"] = exit_flag
            logs["signals"].append(row)
            if not ev.get("valid"):
                logs["events"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "data_tidak_valid",
                                       "pair": pair, "keterangan": ev.get("alasan")})
            elif not binfo[pair][0]:
                logs["events"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "data_tidak_valid",
                                       "pair": pair, "keterangan": "orderbook:" + str(binfo[pair][1])})

        state["last_candle_t"] = target_t
        call["candle_diproses"] = target_t
        call["candle_diproses_utc"] = iso(target_t)
        call["rezim_btc"] = rezim
        call["status"] = "ok"
        eq3, _, _ = mark()
        return _finish(store, state, logs, call, client, clock, eq3)

    except Exception as e:  # noqa: BLE001
        call["status"] = "error"
        logs["errors"].append({"waktu_utc": iso(clock()), "call_id": call_id, "jenis": "exception",
                               "detail": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]})
        for er in getattr(client, "errors", []):
            logs["errors"].append({"waktu_utc": iso(clock()), "call_id": call_id, **er})
        call["selesai_utc"] = iso(clock())
        call["durasi_s"] = round(clock() - t0, 2)
        for n, rows in logs.items():
            if n in ("calls", "errors"):
                continue
            logs[n] = []                         # state tidak disimpan -> log keputusan dibuang agar konsisten
        try:
            store.append("errors", logs["errors"])
            store.append("calls", [call])
        except Exception:  # noqa: BLE001
            pass
        return call
    finally:
        try:
            if store.get("lock") == call_id:
                store.delete("lock")
        except Exception:  # noqa: BLE001
            pass


def _order(state, call_id, candle_t, pair, side, fill, info, fee, fr, p, m, signal_price, alasan, pid, clock):
    dec = cost_decomposition(side, fill["qty"], fill["avg"], info, signal_price)
    now = clock()
    return {
        "waktu_utc": iso(now), "ts": round(now, 3), "call_id": call_id, "candle_t": candle_t, "pair": pair,
        "sisi": side, "qty": fill["qty"], "best_bid": info["bid"], "best_ask": info["ask"], "mid": info["mid"],
        "harga_fill_avg": fill["avg"], "notional_idr": fill["notional"], "fee_idr": fee,
        "fee_persen_dipakai": fr * 100, "fee_persen_resmi": fee_resmi(p, side), "fee_persen_api": m["fee_api"],
        "harga_sinyal": signal_price, "selisih_vs_sinyal_pct": (fill["avg"] / signal_price - 1) * 100,
        **dec, "level_terpakai": fill["levels"], "parsial": fill["parsial"], "alasan": alasan, "posisi_id": pid,
    }


def _finish(store, state, logs, call, client, clock, equity):
    for er in getattr(client, "errors", []):
        logs["errors"].append({"waktu_utc": iso(clock()), "call_id": call["id"], **er})
    call["equity"] = round(equity, 2)
    call["kas"] = round(state["kas"], 2)
    call["n_posisi"] = len(state["posisi"])
    call["posisi"] = {k: {"qty": v["qty"], "bid": state["last_bid"].get(k)} for k, v in state["posisi"].items()}
    call["n_request"] = getattr(client, "n_requests", None)
    call["n_error_api"] = len(getattr(client, "errors", []))
    call["selesai_utc"] = iso(clock())
    call["durasi_s"] = round(clock() - call["mulai_ts"], 2)
    logs["calls"].append(call)
    store.commit(state, logs)
    return call
