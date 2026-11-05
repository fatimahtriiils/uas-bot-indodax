"""Kumpulkan snapshot order book (top-20) tiap pasangan di LAPTOP, format sama dengan log bot.
Bot di Vercel juga menyimpan snapshot setiap panggilan; skrip ini untuk masa persiapan.

  python tools/snapshot_orderbook.py            # sekali
  python tools/snapshot_orderbook.py --loop 3600   # tiap jam (biarkan jalan)
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import ROOT, load_config  # noqa: E402
from core.indodax import Indodax  # noqa: E402


def once(cfg):
    c = Indodax(timeout=10, retries=3, min_interval=0.4)
    os.makedirs(os.path.join(ROOT, "data", "snapshots"), exist_ok=True)
    now = time.time()
    fn = os.path.join(ROOT, "data", "snapshots", f"laptop_{dt.datetime.utcfromtimestamp(now):%Y%m%d}.jsonl")
    with open(fn, "a") as f:
        for pair in dict.fromkeys(cfg["pairs"] + [cfg["pair_rezim"]]):
            b = c.depth(pair)
            ts = time.time()
            f.write(json.dumps({"waktu_utc": dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": round(ts, 3),
                                "call_id": "laptop", "pair": pair, "bids": (b or {}).get("bids", [])[:20],
                                "asks": (b or {}).get("asks", [])[:20]}) + "\n")
    print(f"{dt.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z snapshot tersimpan ({fn}); error: {len(c.errors)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config(require_fee=False)
    while True:
        once(cfg)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
