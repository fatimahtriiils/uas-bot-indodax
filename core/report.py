"""Menyusun SEMUA angka dashboard dari log mentah + hasil backtest.
Dashboard (Vercel & offline) hanya memformat angka dari JSON ini; tidak ada angka yang ditulis manual."""
import datetime as dt
import statistics as stt

from . import metrics as M
from .config import param_hash
from .costs import summarize
from .execution import walk_buy_idr, fee_rate


def parse_iso(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _ts(row):
    return row.get("ts") or row.get("mulai_ts") or parse_iso(row.get("waktu_utc")) or 0


def _down(points, n=700):
    if len(points) <= n:
        return points
    k = len(points) / n
    out = [points[int(i * k)] for i in range(n)]
    return out + [points[-1]] if out[-1] != points[-1] else out


def forward_window(cfg, logs):
    a = parse_iso(cfg.get("forward_mulai"))
    b = parse_iso(cfg.get("forward_selesai"))
    calls = logs.get("calls", [])
    if a is None and calls:
        a = min(c["mulai_ts"] for c in calls)
    return a, b


def build_report(cfg, logs, bt=None, btw=None, costs=None, now_ts=None, info=None):
    p = cfg
    tf = p["timeframe_menit"] * 60
    modal = float(p["modal_awal"])
    fa, fb = forward_window(cfg, logs)
    now_ts = now_ts or dt.datetime.now(dt.timezone.utc).timestamp()
    fend = min(fb, now_ts) if fb else now_ts
    inwin = lambda ts: (fa is None or ts >= fa) and (fb is None or ts <= fb)  # noqa: E731

    calls = sorted([c for c in logs.get("calls", []) if inwin(c.get("mulai_ts", 0))], key=lambda c: c["mulai_ts"])
    # candle masuk jendela bila waktu TUTUP-nya di [mulai, selesai)
    candle_in = lambda t: t is not None and (fa is None or t + tf >= fa) and (fb is None or t + tf < fb)  # noqa: E731
    orders = sorted([o for o in logs.get("orders", []) if candle_in(o.get("candle_t"))], key=_ts)
    signals = [s for s in logs.get("signals", []) if candle_in(s.get("candle_t"))]
    errors = [e for e in logs.get("errors", []) if inwin(parse_iso(e.get("waktu_utc")) or 0)]
    events = [e for e in logs.get("events", []) if inwin(parse_iso(e.get("waktu_utc")) or 0)]
    snaps_all = [s for s in logs.get("snapshots", []) if fb is None or s.get("ts", 0) <= fb]
    snaps = [s for s in snaps_all if inwin(s.get("ts", 0))]
    fr = fee_rate(p, p.get("fee_api_taker_persen_dicatat"), "buy") if isinstance(p.get("fee_taker_beli_persen_resmi"), (int, float)) else None

    R = {"dibuat_utc": dt.datetime.fromtimestamp(now_ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "rumus": M.RUMUS, "info": info or {},
         "strategi": {k: p.get(k) for k in ("versi", "nama", "timeframe_menit", "pairs", "pair_rezim", "breakout_n",
                                            "sma_tren", "slope_lookback", "slope_min", "atr_len", "atr_rasio_len",
                                            "atr_rasio_max", "hold_bars", "stop_atr_mult", "maks_posisi",
                                            "maks_porsi_per_posisi", "daily_loss_limit", "modal_awal", "min_order_idr",
                                            "maks_spread", "fee_taker_beli_persen_resmi", "fee_taker_jual_persen_resmi", "fee_rincian", "fee_sumber_url", "fee_tanggal_akses",
                                            "fee_api_taker_persen_dicatat", "fee_api_tanggal_akses",
                                            "backtest_mulai", "backtest_selesai", "forward_mulai", "forward_selesai")},
         "param_hash": param_hash(cfg),
         "jumlah_baris_log": {k: len(v) for k, v in logs.items()}}
    hashes = sorted({c.get("param_hash") for c in calls if c.get("param_hash")})
    R["param_hash_di_log"] = hashes

    # ------------------------------------------------------------- forward test
    eq_pts = [(c["mulai_ts"], c["equity"]) for c in calls if c.get("equity") is not None]
    fwd = M.curve_stats(modal, eq_pts)
    ftr, fopen = M.trades_from_orders(orders)
    fwd["trade"] = M.trade_stats(ftr)
    fwd["daftar_trade"] = ftr
    fwd["posisi_terbuka"] = fopen
    fwd["kurva"] = _down([[t, v] for t, v in eq_pts])
    fwd["equity_harian"] = [e for e in logs.get("equity", []) if inwin(parse_iso(e.get("waktu_utc")) or 0)]
    # benchmark BTC/IDR buy & hold di order book: beli di ask (habiskan level) pada snapshot pertama, nilai di best bid
    rp = p["pair_rezim"]
    bs = sorted([s for s in snaps if s.get("pair") == rp and s.get("asks") and s.get("bids")], key=lambda s: s["ts"])
    if bs and fr is not None:
        f0 = walk_buy_idr(bs[0]["asks"], modal / (1 + fr), 8)
        pts = [(s["ts"], f0["qty"] * s["bids"][0][0]) for s in bs]
        fwd["benchmark_btc"] = {**M.curve_stats(modal, pts), "harga_beli_avg": f0["avg"], "qty": f0["qty"],
                                "kurva": _down([[t, v] for t, v in pts]),
                                "metode": "beli Rp modal/(1+fee) di ask snapshot pertama (habiskan level), nilai = qty x best bid tiap snapshot"}
    R["forward"] = fwd
    R["forward"]["jendela"] = {"mulai": fa, "selesai": fb}

    # ------------------------------------------------------------- operasional
    sched = []
    if fa:
        h = int(fa // 3600) * 3600
        while h <= fend:
            for m in p.get("jadwal_cron_menit", [7]):
                t = h + m * 60
                if fa < t <= fend:
                    sched.append(t)
            h += 3600
    offs = [c["candle_diproses"] % tf for c in logs.get("calls", []) if c.get("candle_diproses") is not None]
    off = max(set(offs), key=offs.count) if offs else 0
    exp = [t for t in range(int(((fa or 0) - off) // tf) * tf + off - tf, int(fend) + 1, tf) if fa and candle_in(t) and t + tf <= fend]
    on_time = {}
    for c in calls:
        cd = c.get("candle_diproses")
        if cd is not None and c["mulai_ts"] < cd + 2 * tf:
            on_time[cd] = c["mulai_ts"]
    exp = [t for t in exp if t + 2 * tf <= now_ts or t in on_time]   # tenggat (candle berikutnya tutup) sudah lewat
    proc = [t for t in exp if t in on_time]
    delays = [c["mulai_ts"] - (c["candle_diproses"] + tf) for c in calls if c.get("candle_diproses") is not None]
    # Slot terjadwal dianggap terlaksana bila ada panggilan dari penjadwal mana pun
    # (cron-job.org / GitHub Actions) dalam 30 menit setelah slot. Panggilan dobel tidak dihitung dua kali.
    sumber_jadwal = ("github", "cron", "cronjob")
    jc = sorted(c["mulai_ts"] for c in calls if c.get("sumber") in sumber_jadwal)
    sched_delay, slot_ok = [], 0
    for t in sched:
        hit = [x for x in jc if t <= x < t + 1800]
        if hit:
            slot_ok += 1
            sched_delay.append(hit[0] - t)
    by_status, by_src = {}, {}
    for c in calls:
        by_status[c.get("status")] = by_status.get(c.get("status"), 0) + 1
        by_src[c.get("sumber")] = by_src.get(c.get("sumber"), 0) + 1
    err_by = {}
    for e in errors:
        err_by[e.get("jenis")] = err_by.get(e.get("jenis"), 0) + 1
    ev_by = {}
    for e in events:
        ev_by[e.get("jenis")] = ev_by.get(e.get("jenis"), 0) + 1
    R["operasional"] = {
        "panggilan_terjadwal": len(sched),
        "panggilan_terlaksana": len(calls),
        "panggilan_terjadwal_terlaksana": slot_ok,
        "definisi_terlaksana": "slot jadwal (menit jadwal_cron_menit tiap jam) yang diikuti panggilan penjadwal dalam 30 menit",
        "panggilan_per_sumber": by_src, "panggilan_per_status": by_status,
        "candle_seharusnya": len(exp), "candle_tepat_waktu": len(proc),
        "uptime": (len(proc) / len(exp)) if exp else None,
        "candle_tidak_diproses": [t for t in exp if t not in on_time],
        "tunda_proses_detik": {"median": stt.median(delays) if delays else None, "maks": max(delays) if delays else None},
        "keterlambatan_jadwal_detik": {"median": stt.median(sched_delay) if sched_delay else None,
                                       "maks": max(sched_delay) if sched_delay else None, "n": len(sched_delay)},
        "durasi_panggilan_detik": {"median": stt.median([c["durasi_s"] for c in calls if c.get("durasi_s") is not None]) if calls else None,
                                   "maks": max([c.get("durasi_s") or 0 for c in calls]) if calls else None},
        "error_api_total": len(errors), "error_per_jenis": err_by,
        "panggilan_dengan_error": sum(1 for c in calls if (c.get("n_error_api") or 0) > 0 or c.get("status") == "error"),
        "event_per_jenis": ev_by,
        "kill_switch": [e for e in events if str(e.get("jenis", "")).startswith("kill_switch")],
        "panggilan_saat_kill_switch": sum(1 for c in calls if c.get("kill_switch")),
        "daily_loss_limit": [e for e in events if e.get("jenis") == "daily_loss_limit"],
        "error_terakhir": errors[-15:],
        "panggilan_terakhir": calls[-30:],
    }

    # ------------------------------------------------------------- backtest penuh
    if bt:
        pts = [(r["t"], r["equity"]) for r in bt["equity_bars"]]
        b = M.curve_stats(modal, pts, with_cagr=True)
        btr, bopen = M.trades_from_orders(bt["orders"])
        b["trade"] = M.trade_stats(btr)
        b["posisi_terbuka"] = bopen
        b["kurva"] = _down([[t, v] for t, v in pts])
        b["trade_terakhir"] = btr[-40:]
        bp = [(r["t"], r["equity"]) for r in bt.get("benchmark", [])]
        if bp:
            b["benchmark_btc"] = {**M.curve_stats(modal, bp, with_cagr=True), "kurva": _down([[t, v] for t, v in bp])}
        b["meta"] = bt.get("meta", {})
        yr = {}
        for t, v in pts:
            y = dt.datetime.fromtimestamp(t, dt.timezone.utc).year
            yr.setdefault(y, [None, None])
            if yr[y][0] is None:
                yr[y][0] = v
            yr[y][1] = v
        prev, tahunan = modal, []
        for y in sorted(yr):
            tahunan.append({"tahun": y, "return": yr[y][1] / prev - 1})
            prev = yr[y][1]
        b["return_tahunan"] = tahunan
        R["backtest"] = b

    # ------------------------------------------------------------- gap backtest vs forward (jendela sama)
    if btw:
        g = {}
        bsig = {(s["pair"], s["candle_t"]): s for s in btw["signals"]}
        fsig = {}
        for s in signals:
            fsig[(s["pair"], s["candle_t"])] = s
        keys = sorted(set(bsig) | set(fsig), key=lambda k: (k[1], k[0]))
        rows, same, comp = [], 0, 0
        for k in keys:
            a, b_ = fsig.get(k), bsig.get(k)
            if a and b_:
                comp += 1
                ok = (a["entry_final"] == b_["entry_final"]) and ((a.get("exit") or None) == (b_.get("exit") or None))
                same += ok
                if not ok or a.get("entry_final") or b_.get("entry_final") or a.get("exit") or b_.get("exit"):
                    rows.append({"pair": k[0], "candle_t": k[1], "cocok": ok,
                                 "bot_entry": a["entry_final"], "bt_entry": b_["entry_final"],
                                 "bot_exit": a.get("exit"), "bt_exit": b_.get("exit"),
                                 "bot_aksi": a.get("aksi"), "bt_aksi": b_.get("aksi"),
                                 "bot_close": a.get("close"), "bt_close": b_.get("close"),
                                 "bot_level": a.get("level"), "bt_level": b_.get("level")})
            else:
                rows.append({"pair": k[0], "candle_t": k[1], "cocok": None,
                             "catatan": "tidak diproses bot (candle terlewat)" if b_ else "tidak ada di backtest jendela",
                             "bt_entry": (b_ or {}).get("entry_final"), "bt_exit": (b_ or {}).get("exit")})
        g["sinyal"] = {"dibandingkan": comp, "identik": same, "persen_identik": same / comp if comp else None,
                       "hanya_backtest": sum(1 for k in keys if k in bsig and k not in fsig),
                       "hanya_bot": sum(1 for k in keys if k in fsig and k not in bsig),
                       "baris_penting": rows[:400]}
        # perbandingan fill per order (pair, candle, sisi)
        bo = {(o["pair"], o["candle_t"], o["sisi"]): o for o in btw["orders"]}
        fills = []
        for o in orders:
            k = (o["pair"], o["candle_t"], o["sisi"])
            x = bo.get(k)
            sp = o["harga_sinyal"]
            q = o["qty"] or 1
            fills.append({
                "pair": o["pair"], "candle_t": o["candle_t"], "sisi": o["sisi"], "waktu_bot": o["waktu_utc"],
                "harga_sinyal": sp, "fill_bot": o["harga_fill_avg"], "fill_bt": x["harga_fill_avg"] if x else None,
                "selisih_fill_pct": ((o["harga_fill_avg"] / x["harga_fill_avg"] - 1) * 100) if x else None,
                "bot_tunda_bps": o["tunda_idr"] / q / sp * 1e4, "bot_spread_bps": o["spread_idr"] / q / sp * 1e4,
                "bot_slip_bps": o["slippage_idr"] / q / sp * 1e4,
                "bt_spread_bps": (x["spread_idr"] / x["qty"] / sp * 1e4) if x else None,
                "bt_slip_bps": (x["slippage_idr"] / x["qty"] / sp * 1e4) if x else None,
            })
        g["fill"] = fills
        def avg(k):
            xs = [f[k] for f in fills if f.get(k) is not None]
            return sum(xs) / len(xs) if xs else None
        g["fill_rata2_bps"] = {k: avg(k) for k in ("bot_tunda_bps", "bot_spread_bps", "bot_slip_bps", "bt_spread_bps", "bt_slip_bps")}
        wpts = [(r["t"], r["equity"]) for r in btw["equity_bars"]]
        ws = M.curve_stats(modal, wpts)
        wtr, _ = M.trades_from_orders(btw["orders"])
        ws["trade"] = M.trade_stats(wtr)
        ws["kurva"] = _down([[t, v] for t, v in wpts])
        g["backtest_jendela"] = ws
        g["selisih_return"] = (fwd.get("return_total") - ws["return_total"]) if fwd.get("return_total") is not None and ws.get("return_total") is not None else None
        g["selisih_jumlah_trade"] = fwd["trade"]["jumlah_trade"] - ws["trade"]["jumlah_trade"]
        g["meta"] = btw.get("meta", {})
        R["gap"] = g

    # ------------------------------------------------------------- spread: asumsi backtest vs seluruh snapshot
    size = p.get("ukuran_order_slippage_idr", 33000000)
    semua_pair = list(dict.fromkeys(p["pairs"] + [rp]))
    R["spread"] = {"asumsi_backtest": (costs or {}).get("pairs"), "asumsi_meta": {k: v for k, v in (costs or {}).items() if k != "pairs"},
                   "seluruh_snapshot": summarize(snaps_all, semua_pair, size),
                   "snapshot_forward": summarize(snaps, semua_pair, size), "ukuran_order_idr": size}
    return R
