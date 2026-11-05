"""Cache candle historis di data/candles/<pair>_<tf>.csv (ditarik di laptop, bukan di Vercel)."""
import csv
import os

from .config import ROOT

DIR = os.path.join(ROOT, "data", "candles")


def path(pair, tf_menit):
    return os.path.join(DIR, f"{pair}_{tf_menit}.csv")


def save_candles(pair, tf_menit, bars):
    os.makedirs(DIR, exist_ok=True)
    with open(path(pair, tf_menit), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for b in bars:
            w.writerow([b["t"], repr(b["o"]), repr(b["h"]), repr(b["l"]), repr(b["c"]), repr(b["v"])])


def load_candles(pair, tf_menit):
    p = path(pair, tf_menit)
    if not os.path.exists(p):
        raise FileNotFoundError(f"{p} belum ada. Jalankan: python tools/fetch_history.py")
    out = []
    with open(p) as f:
        for r in csv.DictReader(f):
            out.append({"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                        "c": float(r["c"]), "v": float(r["v"])})
    out.sort(key=lambda b: b["t"])
    return out
