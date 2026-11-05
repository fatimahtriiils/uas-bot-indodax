"""Pilih universe dengan kriteria likuiditas yang diukur pada AWAL periode backtest (anti survivorship).

Kriteria (tertulis di config/universe_bukti.json):
  1. Pasangan IDR, bukan stablecoin/aset fiat-pegged.
  2. Sudah punya data harian Indodax pada jendela pengukuran (30 hari sebelum backtest_mulai).
  3. Peringkat = median nilai transaksi harian (IDR) pada jendela itu. Ambil K teratas.
  4. Tidak ada hari tanpa transaksi (volume 0) lebih dari 2 pada jendela itu.
Keterbatasan: daftar kandidat berasal dari /api/pairs hari ini; pasangan yang sudah delisting
tidak muncul. Ini dicatat sebagai sisa survivorship bias.

  python tools/select_universe.py --k 5            # tampilkan
  python tools/select_universe.py --k 5 --tulis    # tulis ke config/strategy.json (SEBELUM freeze)
"""
import argparse
import datetime as dt
import json
import os
import statistics as stt
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import CONFIG_PATH, ROOT, load_config  # noqa: E402
from core.indodax import Indodax  # noqa: E402
from tools.fetch_history import fetch_back, to_ts  # noqa: E402

STABLE = {"usdt", "usdc", "dai", "busd", "tusd", "fdusd", "idrt", "bidr", "usdd", "pyusd", "eurt", "xsgd", "usde"}


def volume_unit(client, pair, daily):
    """Cek apakah kolom Volume history_v2 dalam koin (base) atau IDR, dengan membandingkan
    candle harian terakhir terhadap vol_idr ticker_all (24 jam)."""
    t = client.ticker_all() or {}
    tk = (t.get("tickers") or {}).get(pair.replace("idr", "_idr")) or {}
    try:
        vol_idr = float(tk.get("vol_idr"))
    except (TypeError, ValueError):
        return "koin", None
    last = daily[-1]
    as_coin = last["v"] * last["c"]
    unit = "koin" if abs(as_coin / vol_idr - 1) < abs(last["v"] / vol_idr - 1) else "idr"
    return unit, {"vol_idr_ticker": vol_idr, "v_x_close": as_coin, "v_mentah": last["v"]}


def main():
    cfg = load_config(require_fee=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--hari", type=int, default=30)
    ap.add_argument("--tulis", action="store_true")
    args = ap.parse_args()
    t_end = to_ts(cfg["backtest_mulai"])
    t_start = t_end - args.hari * 86400
    c = Indodax(timeout=15, retries=4, min_interval=0.4)
    pairs = c.pairs() or {}
    kandidat = sorted(k for k in pairs if k.endswith("idr") and k[:-3] not in STABLE)
    print(f"{len(kandidat)} kandidat pasangan IDR (non-stablecoin) dari /api/pairs")
    unit, bukti_unit = None, None
    rows = []
    for i, pair in enumerate(kandidat, 1):
        try:
            d = fetch_back(c, pair, 1440, t_start, t_end + 86400, verbose=False)
        except RuntimeError:
            continue
        w = [b for b in d if t_start <= b["t"] < t_end]
        if len(w) < args.hari * 0.8:
            continue                          # belum listing / data belum ada di awal backtest
        if unit is None and pair == cfg["pair_rezim"]:
            full = fetch_back(c, pair, 1440, int(dt.datetime.now().timestamp()) - 5 * 86400, int(dt.datetime.now().timestamp()), verbose=False)
            unit, bukti_unit = volume_unit(c, pair, full)
        nilai = [(b["v"] * b["c"]) for b in w]
        rows.append({"pair": pair, "hari_data": len(w), "hari_volume_nol": sum(1 for b in w if b["v"] <= 0),
                     "median_nilai_harian": stt.median(nilai), "_v": [b["v"] for b in w]})
        if i % 20 == 0:
            print(f"  {i}/{len(kandidat)} diperiksa")
    if unit == "idr":
        for r in rows:
            r["median_nilai_harian"] = stt.median(r["_v"])
    for r in rows:
        r.pop("_v")
    rows = [r for r in rows if r["hari_volume_nol"] <= 2]
    rows.sort(key=lambda r: -r["median_nilai_harian"])
    pilih = [r["pair"] for r in rows[:args.k]]
    if cfg["pair_rezim"] not in pilih:
        print(f"Catatan: {cfg['pair_rezim']} tidak masuk top-{args.k}; tetap dipakai sebagai filter rezim & benchmark.")
    bukti = {"dibuat_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "jendela_ukur": [dt.datetime.fromtimestamp(t_start, dt.timezone.utc).strftime("%Y-%m-%d"),
                              dt.datetime.fromtimestamp(t_end, dt.timezone.utc).strftime("%Y-%m-%d")],
             "kriteria": __doc__.split("Keterbatasan")[0].strip(), "satuan_volume": unit, "bukti_satuan": bukti_unit,
             "stablecoin_dikecualikan": sorted(STABLE), "peringkat": rows[:30], "terpilih": pilih,
             "keterbatasan": "Kandidat dari /api/pairs saat ini; pasangan yang sudah delisting tidak ikut."}
    with open(os.path.join(ROOT, "config", "universe_bukti.json"), "w") as f:
        json.dump(bukti, f, indent=1)
    print("\nPeringkat likuiditas di awal backtest:")
    for r in rows[:15]:
        print(f"  {r['pair']:<12} median nilai harian Rp{r['median_nilai_harian']:,.0f}  ({r['hari_data']} hari)")
    print("Terpilih:", pilih)
    if args.tulis:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
        raw["pairs"] = pilih
        with open(CONFIG_PATH, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        print("config/strategy.json diperbarui.")


if __name__ == "__main__":
    main()
