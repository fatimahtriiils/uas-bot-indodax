"""Rumus metrik. Satu tempat, dipakai dashboard Vercel (/api/report) dan hitung_ulang.py."""
from collections import defaultdict

RUMUS = {
    "return_total": "equity_akhir / modal_awal - 1",
    "cagr": "(equity_akhir / modal_awal) ^ (365.25 / jumlah_hari) - 1",
    "max_drawdown": "min_t( equity_t / max_{s<=t} equity_s - 1 )",
    "profit_factor": "jumlah laba bersih trade menang / |jumlah rugi bersih trade kalah|",
    "win_rate": "trade dengan laba bersih > 0 / jumlah trade tertutup",
    "rata_untung": "rata-rata laba bersih (Rp dan %) trade menang",
    "rata_rugi": "rata-rata rugi bersih (Rp dan %) trade kalah",
    "porsi_biaya": "(fee + spread + slippage) / laba kotor; laba kotor = laba bersih + fee + spread + slippage (= laba di harga mid)",
    "uptime": "candle diproses sebelum candle berikutnya tutup / jumlah candle selama forward test",
    "laba_bersih_trade": "(notional_jual - fee_jual) - (notional_beli + fee_beli)",
}


def max_drawdown(values):
    peak, mdd = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def curve_stats(modal, points, with_cagr=False):
    """points: [(ts, equity)] terurut. Titik awal = modal pada ts pertama."""
    if not points:
        return {"return_total": None, "max_drawdown": None, "n_titik": 0}
    vals = [modal] + [v for _, v in points]
    out = {"modal_awal": modal, "equity_akhir": vals[-1], "return_total": vals[-1] / modal - 1,
           "max_drawdown": max_drawdown(vals), "n_titik": len(points),
           "ts_awal": points[0][0], "ts_akhir": points[-1][0]}
    if with_cagr:
        days = (points[-1][0] - points[0][0]) / 86400
        out["hari"] = days
        out["cagr"] = (vals[-1] / modal) ** (365.25 / days) - 1 if days > 0 and vals[-1] > 0 else None
    return out


def trades_from_orders(orders):
    """Pasangkan order beli & jual lewat posisi_id. Return (trade_tertutup, posisi_terbuka)."""
    g = defaultdict(lambda: {"buy": [], "sell": []})
    for o in orders:
        g[o["posisi_id"]][o["sisi"]].append(o)
    closed, open_ = [], []
    for pid, d in g.items():
        if not d["buy"]:
            continue
        b = d["buy"][0]
        q_in = b["qty"]
        q_out = sum(s["qty"] for s in d["sell"])
        cost_in = b["notional_idr"] + b["fee_idr"]
        biaya_in = b["fee_idr"] + b["spread_idr"] + b["slippage_idr"]
        if not d["sell"] or q_out < q_in * (1 - 1e-9):
            open_.append({"posisi_id": pid, "pair": b["pair"], "masuk_utc": b["waktu_utc"], "qty": q_in - q_out,
                          "harga_masuk": b["harga_fill_avg"], "biaya_masuk_idr": cost_in})
            continue
        proceeds = sum(s["notional_idr"] - s["fee_idr"] for s in d["sell"])
        fee = b["fee_idr"] + sum(s["fee_idr"] for s in d["sell"])
        spread = b["spread_idr"] + sum(s["spread_idr"] for s in d["sell"])
        slip = b["slippage_idr"] + sum(s["slippage_idr"] for s in d["sell"])
        net = proceeds - cost_in
        last = d["sell"][-1]
        closed.append({
            "posisi_id": pid, "pair": b["pair"], "masuk_utc": b["waktu_utc"], "keluar_utc": last["waktu_utc"],
            "candle_masuk": b["candle_t"], "candle_keluar": last["candle_t"], "qty": q_in,
            "harga_masuk": b["harga_fill_avg"], "harga_keluar": last["harga_fill_avg"],
            "alasan_keluar": last["alasan"], "laba_bersih_idr": net, "return_pct": net / cost_in * 100,
            "fee_idr": fee, "spread_idr": spread, "slippage_idr": slip, "biaya_idr": fee + spread + slip,
            "laba_kotor_idr": net + fee + spread + slip, "_biaya_masuk": biaya_in,
        })
    closed.sort(key=lambda x: x["masuk_utc"])
    return closed, open_


def trade_stats(trades):
    n = len(trades)
    win = [t for t in trades if t["laba_bersih_idr"] > 0]
    loss = [t for t in trades if t["laba_bersih_idr"] <= 0]
    sw = sum(t["laba_bersih_idr"] for t in win)
    sl = sum(t["laba_bersih_idr"] for t in loss)
    fee = sum(t["fee_idr"] for t in trades)
    spread = sum(t["spread_idr"] for t in trades)
    slip = sum(t["slippage_idr"] for t in trades)
    gross = sum(t["laba_kotor_idr"] for t in trades)
    avg = lambda xs, k: (sum(x[k] for x in xs) / len(xs)) if xs else None  # noqa: E731
    return {
        "jumlah_trade": n, "jumlah_menang": len(win), "jumlah_kalah": len(loss),
        "win_rate": len(win) / n if n else None,
        "profit_factor": (sw / abs(sl)) if sl < 0 else None,
        "profit_factor_catatan": None if sl < 0 else ("tidak ada trade rugi" if n else "tidak ada trade"),
        "rata_untung_idr": avg(win, "laba_bersih_idr"), "rata_untung_pct": avg(win, "return_pct"),
        "rata_rugi_idr": avg(loss, "laba_bersih_idr"), "rata_rugi_pct": avg(loss, "return_pct"),
        "laba_bersih_idr": sw + sl, "laba_kotor_idr": gross,
        "fee_idr": fee, "spread_idr": spread, "slippage_idr": slip, "biaya_total_idr": fee + spread + slip,
        "porsi_biaya": ((fee + spread + slip) / gross) if gross > 0 else None,
        "porsi_biaya_catatan": None if gross > 0 else "laba kotor <= 0, rasio tidak bermakna; lihat biaya_total_idr",
    }
