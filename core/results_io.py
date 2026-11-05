"""Baca/tulis hasil backtest & log dalam JSON Lines (satu baris = satu record)."""
import json
import os

from .config import ROOT

BT_FILES = ["orders", "signals", "equity_bars", "equity_daily", "events", "benchmark"]


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def save_results(out, folder):
    for k in BT_FILES:
        if k in out:
            write_jsonl(os.path.join(folder, k + ".jsonl"), out[k])
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({**out.get("meta", {}), "posisi_terbuka": out.get("posisi_terbuka", [])}, f, indent=1)


def load_results(folder):
    folder = folder if os.path.isabs(folder) else os.path.join(ROOT, folder)
    meta_p = os.path.join(folder, "meta.json")
    if not os.path.exists(meta_p):
        return None
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    out = {k: read_jsonl(os.path.join(folder, k + ".jsonl")) for k in BT_FILES}
    out["meta"] = meta
    return out


def load_logs_dir(folder):
    from .store import LOGS
    return {n: read_jsonl(os.path.join(folder, n + ".jsonl")) for n in LOGS}
