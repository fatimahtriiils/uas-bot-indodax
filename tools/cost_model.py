"""Hitung median setengah-spread dan slippage per pasangan dari SNAPSHOT TERUKUR -> config/costs.json.
Dipakai backtest. Jalankan SEBELUM freeze, dengan snapshot sampai freeze saja.

  python tools/cost_model.py --sampai 2026-11-05T05:00:00Z
Sumber snapshot: data/snapshots/*.jsonl (laptop) + logs/snapshots.jsonl (ekspor bot, bila ada).
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import COSTS_PATH, ROOT, load_config  # noqa: E402
from core.costs import summarize  # noqa: E402
from core.report import parse_iso  # noqa: E402
from core.results_io import read_jsonl  # noqa: E402


def main():
    cfg = load_config(require_fee=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--sampai", default=None, help="batas waktu snapshot (ISO UTC), = waktu freeze")
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(ROOT, "data", "snapshots", "*.jsonl"))) + [os.path.join(ROOT, "logs", "snapshots.jsonl")]
    snaps = [s for f in files for s in read_jsonl(f)]
    t_to = parse_iso(a.sampai) if a.sampai else None
    pairs = list(dict.fromkeys(cfg["pairs"] + [cfg["pair_rezim"]]))
    size = cfg.get("ukuran_order_slippage_idr", 33000000)
    res = summarize(snaps, pairs, size, t_to=t_to)
    kosong = [p for p, r in res.items() if not r.get("n_valid")]
    if kosong:
        sys.exit(f"Belum ada snapshot valid untuk: {kosong}. Kumpulkan dulu (tools/snapshot_orderbook.py / bot).")
    out = {"dibuat_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "snapshot_sampai": a.sampai,
           "ukuran_order_idr": size, "file_sumber": [os.path.relpath(f, ROOT) for f in files if os.path.exists(f)],
           "metode": "per snapshot: half_spread=(ask-bid)/2/mid; slip_buy = harga rata2 beli Rp ukuran_order (habiskan ask) / best ask - 1; "
                     "slip_sell = 1 - harga rata2 jual (ukuran_order/mid koin) / best bid. Diambil median per pasangan.",
           "pairs": res}
    with open(COSTS_PATH, "w") as f:
        json.dump(out, f, indent=1)
    for p, r in res.items():
        print(f"{p:<10} n={r['n_valid']:<4} half-spread {r['half_spread_median']*1e4:6.1f} bps  slip beli {r['slip_buy_median']*1e4:5.1f} bps  slip jual {r['slip_sell_median']*1e4:5.1f} bps")
    print(f"-> {COSTS_PATH}")


if __name__ == "__main__":
    main()
