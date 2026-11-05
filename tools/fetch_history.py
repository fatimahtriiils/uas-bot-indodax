"""Tarik candle historis Indodax MUNDUR (to = sekarang, lalu geser ke bar paling awal) sampai tanggal awal.
Sekuensial, jeda >= 0.35 dtk (<= 3 request/detik), tanpa paralel. Jalankan di LAPTOP.

Contoh:
  python tools/fetch_history.py                      # semua pair config + pair rezim, tf config
  python tools/fetch_history.py --tf 1440 --pairs btcidr,ethidr --mulai 2023-09-01
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import load_config  # noqa: E402
from core.data import save_candles  # noqa: E402
from core.indodax import Indodax, drop_unclosed  # noqa: E402


def to_ts(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def fetch_back(client, pair, tf, start_ts, end_ts, verbose=True):
    bars, to = {}, end_ts
    while True:
        b = client.candles(pair, tf, start_ts, to)
        if b is None:
            raise RuntimeError(f"Gagal menarik {pair} (lihat error): {client.errors[-1] if client.errors else ''}")
        n_new = sum(1 for x in b if x["t"] not in bars)
        for x in b:
            bars[x["t"]] = x
        if not b or n_new == 0:
            break
        earliest = b[0]["t"]
        if verbose:
            print(f"  {pair}: +{n_new} bar, paling awal {dt.datetime.fromtimestamp(earliest, dt.timezone.utc):%Y-%m-%d %H:%M}")
        if earliest <= start_ts:
            break
        to = earliest
    return [bars[t] for t in sorted(bars) if t >= start_ts]


def main():
    cfg = load_config(require_fee=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=",".join(dict.fromkeys(cfg["pairs"] + [cfg["pair_rezim"]])))
    ap.add_argument("--tf", type=int, default=cfg["timeframe_menit"])
    ap.add_argument("--mulai", default=None, help="YYYY-MM-DD (default: backtest_mulai dikurangi 90 hari untuk pemanasan)")
    args = ap.parse_args()
    start = to_ts(args.mulai) if args.mulai else to_ts(cfg["backtest_mulai"]) - 90 * 86400
    client = Indodax(timeout=15, retries=4, min_interval=0.4)
    srv = client.server_time()
    if srv is None:
        sys.exit("Tidak bisa membaca server_time Indodax.")
    for pair in args.pairs.split(","):
        print(f"Menarik {pair} tf={args.tf} ...")
        bars = drop_unclosed(fetch_back(client, pair, args.tf, start, int(srv)), args.tf, srv)
        save_candles(pair, args.tf, bars)
        if bars:
            a = dt.datetime.fromtimestamp(bars[0]["t"], dt.timezone.utc)
            b = dt.datetime.fromtimestamp(bars[-1]["t"], dt.timezone.utc)
            print(f"  -> {len(bars)} bar, {a:%Y-%m-%d} s/d {b:%Y-%m-%d %H:%M} UTC")
    meta = client.pairs()
    if meta:
        import json
        keep = {k: meta[k] for k in args.pairs.split(",") if k in meta}
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "pairs_meta.json"), "w") as f:
            json.dump({"diakses_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "sumber": "https://indodax.com/api/pairs", **keep}, f, indent=1)
        for k, v in keep.items():
            print(f"  {k}: trade_fee_percent_taker={v.get('trade_fee_percent_taker')} trade_min_base_currency={v.get('trade_min_base_currency')} volume_precision={v.get('volume_precision')}")
    print(f"Request: {client.n_requests}, error: {len(client.errors)}")


if __name__ == "__main__":
    main()
