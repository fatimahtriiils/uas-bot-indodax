"""Statistik spread & slippage dari snapshot order book mentah.
Dipakai tools/cost_model.py (biaya untuk backtest, sebelum freeze) dan report
(perbandingan dengan SELURUH snapshot di refleksi)."""
import statistics as stt

from .execution import walk_qty


def snap_metrics(snap, size_idr):
    bids, asks = snap.get("bids") or [], snap.get("asks") or []
    if not bids or not asks:
        return None
    bid, ask = bids[0][0], asks[0][0]
    if ask <= bid:
        return None
    mid = (bid + ask) / 2
    # beli senilai size_idr: habiskan ask
    qty, sisa = 0.0, size_idr
    for pr, am in asks:
        take = min(am, sisa / pr)
        qty += take
        sisa -= take * pr
        if sisa <= 1e-9:
            break
    out = {"half_spread": (ask - bid) / 2 / mid, "spread": (ask - bid) / mid}
    if sisa <= 1e-6 and qty > 0:
        out["slip_buy"] = (size_idr / qty) / ask - 1
    q_sell = size_idr / mid
    f = walk_qty(bids, q_sell)
    if not f["parsial"] and f["qty"] > 0:
        out["slip_sell"] = 1 - f["avg"] / bid
    return out


def summarize(snapshots, pairs, size_idr, t_from=None, t_to=None):
    res = {}
    for pair in pairs:
        rows = [s for s in snapshots if s.get("pair") == pair
                and (t_from is None or s.get("ts", 0) >= t_from) and (t_to is None or s.get("ts", 0) <= t_to)]
        ms = [m for m in (snap_metrics(s, size_idr) for s in rows) if m]
        if not ms:
            res[pair] = {"n_snapshot": len(rows), "n_valid": 0}
            continue
        def med(k):
            xs = [m[k] for m in ms if k in m]
            return stt.median(xs) if xs else None
        ts = [s.get("ts") for s in rows if s.get("ts")]
        res[pair] = {
            "n_snapshot": len(rows), "n_valid": len(ms),
            "half_spread_median": med("half_spread"), "spread_median": med("spread"),
            "spread_p90": sorted(m["spread"] for m in ms)[int(0.9 * (len(ms) - 1))],
            "slip_buy_median": med("slip_buy") or 0.0, "slip_sell_median": med("slip_sell") or 0.0,
            "ts_awal": min(ts) if ts else None, "ts_akhir": max(ts) if ts else None,
        }
    return res
