"""Backtest memakai fungsi sinyal yang sama dengan bot (core/strategy.py).

  python backtest/run_backtest.py penuh          # backtest_mulai..backtest_selesai -> results/backtest/
  python backtest/run_backtest.py jendela        # forward_mulai..forward_selesai (candle yang sama) -> results/window/
                                                 # (tarik ulang candle dulu setelah forward test selesai)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.backtest_engine import run_backtest  # noqa: E402
from core.config import ROOT, load_config, load_costs  # noqa: E402
from core.data import load_candles  # noqa: E402
from core.execution import QTY_DEC  # noqa: E402
from core.report import parse_iso  # noqa: E402
from core.results_io import save_results  # noqa: E402
from tools.fetch_history import to_ts  # noqa: E402


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "penuh"
    cfg = load_config()
    costs = load_costs()
    if not costs:
        sys.exit("config/costs.json belum ada. Jalankan tools/cost_model.py setelah snapshot terkumpul.")
    pairs = list(dict.fromkeys(cfg["pairs"] + [cfg["pair_rezim"]]))
    candles = {p: load_candles(p, cfg["timeframe_menit"]) for p in pairs}
    qty_dec = {p: QTY_DEC for p in pairs}     # sama dengan bot (lihat core/execution.py)
    if mode == "penuh":
        a, b, out_dir, sig = to_ts(cfg["backtest_mulai"]), to_ts(cfg["backtest_selesai"]), "results/backtest", False
    elif mode == "jendela":
        a, b = parse_iso(cfg["forward_mulai"]), parse_iso(cfg["forward_selesai"])
        if a is None or b is None:
            sys.exit("forward_mulai / forward_selesai belum diisi (ISO UTC).")
        out_dir, sig = "results/window", True
    else:
        sys.exit(__doc__)
    last = min(c[-1]["t"] for c in candles.values()) + cfg["timeframe_menit"] * 60
    if last < b:
        print(f"PERINGATAN: data candle hanya sampai {last} < akhir {b}. Tarik ulang dengan tools/fetch_history.py.")
    out = run_backtest(cfg, candles, costs, a, b, simpan_sinyal=sig, qty_dec=qty_dec)
    save_results(out, os.path.join(ROOT, out_dir))
    n_buy = sum(1 for o in out["orders"] if o["sisi"] == "buy")
    eq = out["equity_bars"][-1]["equity"] if out["equity_bars"] else cfg["modal_awal"]
    print(f"{mode}: {out['meta']['n_candle']} candle, {n_buy} pembelian, equity akhir Rp{eq:,.0f} -> {out_dir}/")


if __name__ == "__main__":
    main()
