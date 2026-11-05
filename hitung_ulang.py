"""HITUNG ULANG semua angka dashboard dari log mentah + hasil backtest.

  python hitung_ulang.py
      membaca logs/*.jsonl (hasil `tools/storage_admin.py export`), results/backtest, results/window,
      config/costs.json -> menulis dist/report.json dan dist/dashboard_offline.html (mandiri, tanpa internet),
      lalu mencetak angka-angka utama.

  python hitung_ulang.py --banding report_vercel.json
      membandingkan setiap angka dengan JSON yang diunduh dari https://<app>.vercel.app/api/report
      (memakai waktu pembuatan JSON itu sebagai 'sekarang'). Keluar kode 1 bila ada angka berbeda.
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core.config import load_config, load_costs  # noqa: E402
from core.report import build_report, parse_iso  # noqa: E402
from core.results_io import load_logs_dir, load_results  # noqa: E402

ABAIKAN = {"dibuat_utc", "info", "jumlah_baris_log"}


def flat(x, pre=""):
    if isinstance(x, dict):
        for k, v in x.items():
            if pre == "" and k in ABAIKAN:
                continue
            yield from flat(v, f"{pre}.{k}" if pre else k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from flat(v, f"{pre}[{i}]")
    else:
        yield pre, x


def sama(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    return a == b


def pct(x):
    return "—" if x is None else f"{x * 100:,.2f}%"


def rp(x):
    return "—" if x is None else f"Rp{x:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=os.path.join(ROOT, "logs"))
    ap.add_argument("--banding", default=None)
    a = ap.parse_args()
    cfg = load_config(require_fee=False)
    logs = load_logs_dir(a.logs)
    bt, btw, costs = load_results("results/backtest"), load_results("results/window"), load_costs()
    ref = None
    if a.banding:
        ref = json.load(open(a.banding))
        now = parse_iso(ref["dibuat_utc"])
    else:
        calls = logs.get("calls") or []
        fb = parse_iso(cfg.get("forward_selesai"))
        now = max([c["mulai_ts"] for c in calls] + [0]) or None
        if fb and now and now > fb:
            now = fb
    rep = build_report(cfg, logs, bt, btw, costs, now_ts=now, info={"sumber": "hitung_ulang.py (offline dari logs/)"})

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    with open(os.path.join(ROOT, "dist", "report.json"), "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    tpl = open(os.path.join(ROOT, "public", "index.html"), encoding="utf-8").read()
    marker = "null; /*__EMBED__*/"
    assert marker in tpl, "penanda embed tidak ditemukan di public/index.html"
    data = json.dumps(rep, ensure_ascii=False, default=str).replace("</", "<\\/")
    with open(os.path.join(ROOT, "dist", "dashboard_offline.html"), "w", encoding="utf-8") as f:
        f.write(tpl.replace(marker, data + "; /*__EMBED__*/"))

    F, B, O = rep["forward"], rep.get("backtest"), rep["operasional"]
    print("=== FORWARD TEST ===")
    print(f"Return total      {pct(F.get('return_total'))}   MDD {pct(F.get('max_drawdown'))}   equity akhir {rp(F.get('equity_akhir'))}")
    t = F["trade"]
    print(f"Trade {t['jumlah_trade']}  win rate {pct(t['win_rate'])}  PF {t['profit_factor'] if t['profit_factor'] is not None else t['profit_factor_catatan']}")
    print(f"Rata untung {rp(t['rata_untung_idr'])}  rata rugi {rp(t['rata_rugi_idr'])}  porsi biaya {pct(t['porsi_biaya'])}")
    if F.get("benchmark_btc"):
        print(f"BTC/IDR buy&hold  {pct(F['benchmark_btc']['return_total'])}  MDD {pct(F['benchmark_btc']['max_drawdown'])}")
    if B:
        print("=== BACKTEST ===")
        print(f"Return {pct(B['return_total'])}  CAGR {pct(B.get('cagr'))}  MDD {pct(B['max_drawdown'])}  trade {B['trade']['jumlah_trade']}  "
              f"win {pct(B['trade']['win_rate'])}  PF {B['trade']['profit_factor']}  porsi biaya {pct(B['trade']['porsi_biaya'])}")
        if B.get("benchmark_btc"):
            print(f"BTC/IDR buy&hold  return {pct(B['benchmark_btc']['return_total'])}  CAGR {pct(B['benchmark_btc'].get('cagr'))}  MDD {pct(B['benchmark_btc']['max_drawdown'])}")
    print("=== OPERASIONAL ===")
    print(f"Panggilan terjadwal {O['panggilan_terjadwal']}  terlaksana (terjadwal) {O['panggilan_terjadwal_terlaksana']}  total {O['panggilan_terlaksana']}")
    print(f"Uptime {pct(O['uptime'])} ({O['candle_tepat_waktu']}/{O['candle_seharusnya']} candle)  error API {O['error_api_total']}  kill switch {len(O['kill_switch'])} event")
    if rep.get("gap"):
        s = rep["gap"]["sinyal"]
        print(f"=== GAP === sinyal identik {s['identik']}/{s['dibandingkan']} ({pct(s['persen_identik'])}); selisih return {pct(rep['gap']['selisih_return'])}")
    print("-> dist/report.json, dist/dashboard_offline.html")

    if ref is not None:
        mine = dict(flat(rep))
        theirs = dict(flat(ref))
        beda = [(k, mine.get(k), theirs.get(k)) for k in sorted(set(mine) | set(theirs)) if not sama(mine.get(k), theirs.get(k))]
        print(f"\nPerbandingan dengan {a.banding}: {len(mine)} nilai, {len(beda)} berbeda")
        for k, x, y in beda[:40]:
            print(f"  {k}: hitung_ulang={x!r}  vercel={y!r}")
        sys.exit(1 if beda else 0)


if __name__ == "__main__":
    main()
