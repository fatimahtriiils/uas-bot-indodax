"""Mesin backtest. Memanggil fungsi yang SAMA dengan bot: strategy.evaluate, regime_ok,
plan, exit_signal, target_size, entry_final. Yang berbeda hanya eksekusi: candle tidak
berisi bid/ask, jadi harga fill = close candle sinyal +/- (setengah spread + slippage)
yang diukur dari snapshot order book (config/costs.json)."""
import math

from . import strategy as S
from .bot import iso, wib_date
from .config import param_hash
from .execution import fee_rate, floor_dec


def _cost(costs, pair):
    c = (costs or {}).get("pairs", {}).get(pair)
    if not c:
        raise ValueError(f"Biaya terukur untuk {pair} tidak ada di config/costs.json. Jalankan tools/cost_model.py.")
    return c["half_spread_median"], c["slip_buy_median"], c["slip_sell_median"]


def run_backtest(cfg, candles, costs, start_ts, end_ts, simpan_sinyal=False, qty_dec=None):
    """candles: {pair: [bar,...]} terurut. Candle t diproses bila start_ts <= t+tf < end_ts (waktu tutup).
    Return dict berisi orders, signals, equity_bars, equity_daily, events, benchmark, meta."""
    p = cfg
    tf = p["timeframe_menit"] * 60
    fr_api = p.get("fee_api_taker_persen_dicatat")
    fr_buy, fr_sell = fee_rate(p, fr_api, "buy"), fee_rate(p, fr_api, "sell")
    idx = {pair: {b["t"]: k for k, b in enumerate(bs)} for pair, bs in candles.items()}
    rp0 = candles.get(p["pair_rezim"]) or next(iter(candles.values()))
    off = rp0[0]["t"] % tf if rp0 else 0               # perataan candle diambil dari data, bukan diasumsikan
    first = math.ceil((start_ts - tf - off) / tf) * tf + off
    timeline = [t for t in range(first, end_ts + 1, tf) if start_ts <= t + tf < end_ts]

    st = {"kas": float(p["modal_awal"]), "posisi": {}, "seq": 0, "hari": None, "equity_awal_hari": None, "dll_hari": None}
    out = {"orders": [], "signals": [], "equity_bars": [], "equity_daily": [], "events": []}
    rp = p["pair_rezim"]
    hs_rp, sb_rp, _ = _cost(costs, rp) if rp in (costs or {}).get("pairs", {}) else (0, 0, 0)
    bench = None
    last_close = {}

    for t in timeline:
        ex = t + tf
        bars_now, evals = {}, {}
        for pair in p["pairs"]:
            k = idx[pair].get(t)
            if k is None:
                evals[pair] = {"valid": False, "alasan": "candle_hilang", "entry_raw": False}
                continue
            bars_now[pair] = candles[pair][k]
            last_close[pair] = candles[pair][k]["c"]
            evals[pair] = S.evaluate(candles[pair], k, p)
        j = idx.get(rp, {}).get(t)
        rezim = S.regime_ok(candles.get(rp, []), j, p)

        def mark():
            v = 0.0
            for pr, ps in st["posisi"].items():
                hs, _, _ = _cost(costs, pr)
                v += ps["qty"] * last_close.get(pr, ps["entry_avg"]) * (1 - hs)
            return st["kas"] + v

        eq = mark()
        hari = wib_date(ex)
        if st["hari"] != hari:
            out["equity_daily"].append({"tanggal_wib": hari, "waktu_utc": iso(ex), "equity": round(eq, 2)})
            st["hari"], st["equity_awal_hari"] = hari, eq

        def cek_dll(e):
            if st["dll_hari"] != hari and e <= st["equity_awal_hari"] * (1 - p["daily_loss_limit"]):
                st["dll_hari"] = hari
                out["events"].append({"waktu_utc": iso(ex), "jenis": "daily_loss_limit"})
        cek_dll(eq)

        boleh = st["dll_hari"] != hari
        rencana = S.plan(st["posisi"], evals, {k: v for k, v in bars_now.items() if k in st["posisi"]}, p, boleh, rezim)
        aksi = {}
        for pair, why in rencana["exits"]:
            ps = st["posisi"].pop(pair)
            c = bars_now[pair]["c"]
            hs, _, ss = _cost(costs, pair)
            price = c * (1 - hs - ss)
            notional = ps["qty"] * price
            fee = notional * fr_sell
            st["kas"] += notional - fee
            out["orders"].append(_order(ex, t, pair, "sell", ps["qty"], c, hs, price, notional, fee, fr_sell, why, ps["posisi_id"]))
            aksi[pair] = "jual:" + why

        eq2 = mark()
        cek_dll(eq2)
        if st["dll_hari"] == hari:
            for pr in rencana["entries"]:
                rencana["ditolak"][pr] = "daily_loss_limit"
            rencana["entries"] = []
        for pair in rencana["entries"]:
            ev = evals[pair]
            hs, sb, _ = _cost(costs, pair)
            price = ev["close"] * (1 + hs + sb)
            size = S.target_size(eq2, st["kas"], p)
            dec = (qty_dec or {}).get(pair, 8)
            qty = floor_dec((size / (1 + fr_buy)) / price, dec)
            notional = qty * price
            if qty <= 0 or notional < p["min_order_idr"]:
                rencana["ditolak"][pair] = "di_bawah_minimum"
                continue
            fee = notional * fr_buy
            st["kas"] -= notional + fee
            st["seq"] += 1
            pid = f"B{st['seq']:05d}"
            out["orders"].append(_order(ex, t, pair, "buy", qty, ev["close"], hs, price, notional, fee, fr_buy, "sinyal_masuk", pid))
            st["posisi"][pair] = {"posisi_id": pid, "qty": qty, "entry_avg": price, "sinyal_t": t,
                                  "sinyal_close": ev["close"], "sinyal_atr": ev["atr"]}
            aksi[pair] = "beli"

        if simpan_sinyal:
            for pair in p["pairs"]:
                ev = evals[pair]
                row = {"candle_t": t, "candle_utc": iso(t), "pair": pair, "valid": ev.get("valid", False),
                       "alasan_tidak_valid": ev.get("alasan"), "rezim": rezim, "entry_raw": bool(ev.get("entry_raw")),
                       "entry_final": S.entry_final(ev, rezim),
                       "aksi": aksi.get(pair) or ("tolak:" + rencana["ditolak"][pair] if pair in rencana["ditolak"] else "-")}
                for kk in ("close", "level", "sma", "slope", "atr", "atrp", "atrp_mean"):
                    if kk in ev:
                        row[kk] = ev[kk]
                ex_flag = None
                if pair in bars_now:
                    if aksi.get(pair, "").startswith("jual"):
                        ex_flag = aksi[pair].split(":", 1)[1]
                    elif pair in st["posisi"]:
                        ex_flag = S.exit_signal(st["posisi"][pair], bars_now[pair], p)[1]
                row["exit"] = ex_flag
                out["signals"].append(row)

        eq3 = mark()
        out["equity_bars"].append({"t": ex, "waktu_utc": iso(ex), "equity": round(eq3, 2), "n_posisi": len(st["posisi"])})

        # benchmark buy & hold BTC/IDR: beli di candle pertama (close + biaya terukur + fee), nilai di bid proxy
        kb = idx.get(rp, {}).get(t)
        if kb is not None:
            cb = candles[rp][kb]["c"]
            if bench is None:
                price = cb * (1 + hs_rp + sb_rp)
                q = (p["modal_awal"] / (1 + fr_buy)) / price
                bench = {"qty": q, "rows": []}
            bench["rows"].append({"t": ex, "equity": round(bench["qty"] * cb * (1 - hs_rp), 2)})

    out["benchmark"] = bench["rows"] if bench else []
    out["posisi_terbuka"] = [{"pair": k, **v} for k, v in st["posisi"].items()]
    out["meta"] = {"param_hash": param_hash(cfg), "mulai_utc": iso(start_ts), "selesai_utc": iso(end_ts),
                   "n_candle": len(timeline), "fee_persen_beli": fr_buy * 100, "fee_persen_jual": fr_sell * 100, "biaya": costs,
                   "pairs": p["pairs"], "timeframe_menit": p["timeframe_menit"]}
    return out


def _order(ex, t, pair, side, qty, close, hs, price, notional, fee, fr, alasan, pid):
    bid, ask = close * (1 - hs), close * (1 + hs)
    if side == "buy":
        dec = {"tunda_idr": 0.0, "spread_idr": qty * (ask - close), "slippage_idr": qty * (price - ask)}
    else:
        dec = {"tunda_idr": 0.0, "spread_idr": qty * (close - bid), "slippage_idr": qty * (bid - price)}
    return {"waktu_utc": iso(ex), "ts": ex, "candle_t": t, "pair": pair, "sisi": side, "qty": qty,
            "best_bid": bid, "best_ask": ask, "mid": close, "harga_fill_avg": price, "notional_idr": notional,
            "fee_idr": fee, "fee_persen_dipakai": fr * 100, "harga_sinyal": close,
            "selisih_vs_sinyal_pct": (price / close - 1) * 100, **dec, "alasan": alasan, "posisi_id": pid}
