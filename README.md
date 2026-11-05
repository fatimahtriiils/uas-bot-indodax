# Bot Dry Run Indodax — UAS Algoritma Trading

Fatimah Tri Lestari · 25110200019

Bot long-only yang membaca harga dan order book **asli** Indodax (API publik, tanpa API key), mengeksekusi order
**simulasi** di bid/ask, dan mencatat setiap keputusan. Semua angka dashboard dihitung oleh `core/report.py` dari log mentah;
`hitung_ulang.py` menghasilkan ulang angka yang sama secara offline.

- Link Vercel: ISI
- Link repositori: ISI
- Commit freeze: ISI hash · ISI waktu (WIB)
- Strategi & catatan perubahan dari UTS: [STRATEGI.md](STRATEGI.md)
- Refleksi: [REFLEKSI.md](REFLEKSI.md)

## Struktur

```
config/strategy.json     SEMUA aturan, parameter, universe (dibekukan saat freeze)
config/costs.json        median spread & slippage dari snapshot (dibuat tools/cost_model.py)
core/strategy.py         FUNGSI SINYAL BERSAMA bot & backtest
core/bot.py              satu panggilan bot: data → validasi → sinyal → eksekusi simulasi → state + log
core/backtest_engine.py  backtest (memanggil fungsi strategi yang sama)
core/execution.py        fill di order book (ask/bid, habiskan level), fee, minimum order
core/indodax.py          klien API publik + timeout + retry terbatas
core/store.py            Upstash Redis (state, kill switch, log)
core/metrics.py, report.py  rumus metrik dan penyusun angka dashboard
api/tick.py              endpoint bot (wajib CRON_SECRET)
api/report.py            JSON angka dashboard (publik, hanya baca)
api/kill.py              kill switch manual (wajib CRON_SECRET)
public/index.html        dashboard (Vercel) — juga template file offline
hitung_ulang.py          hitung ulang semua angka dari logs/ → dist/dashboard_offline.html
backtest/run_backtest.py backtest penuh (≥3 tahun) & backtest jendela forward test
tools/                   tarik data, pilih universe, snapshot order book, model biaya, admin storage
tests/test_bot.py        uji paritas sinyal bot vs backtest, idempotency, kill switch, retry, order book
logs/                    log mentah forward test (hasil ekspor) — ikut dikumpulkan
results/                 hasil backtest (jsonl) — ikut dikumpulkan
```

## Menjalankan di laptop (Python 3.10+, tanpa pip install)

```bash
python -m unittest tests/test_bot.py -v           # uji (data sintetis, hanya untuk menguji kode)
python tools/snapshot_orderbook.py --loop 3600    # kumpulkan snapshot order book (biarkan jalan)
python tools/select_universe.py --k 5 --tulis     # universe dari likuiditas awal backtest
python tools/fetch_history.py                     # candle 4 jam ≥3 tahun, ditarik mundur, ≤3 req/dtk
python tools/cost_model.py --sampai <freeze ISO UTC>
python backtest/run_backtest.py penuh             # → results/backtest/
```

## Deploy ke Vercel

1. Push repo ke GitHub (publik), lalu Import Project di Vercel (Framework: Other).
2. Vercel → Storage / Marketplace → **Upstash Redis** (gratis) → Connect ke project. Env `KV_REST_API_URL` dan `KV_REST_API_TOKEN` terisi otomatis.
3. Vercel → Settings → Environment Variables: `CRON_SECRET` = string acak ≥32 karakter (`python -c "import secrets;print(secrets.token_hex(24))"`). Redeploy.
4. GitHub → Settings → Secrets → Actions: `CRON_SECRET` (sama) dan `TICK_URL` = `https://<app>.vercel.app/api/tick`.
5. Uji koneksi: Actions → tick-bot → Run workflow. Buka `https://<app>.vercel.app` (dashboard) dan `/api/report`.
6. Penjadwal utama: cron-job.org memanggil `TICK_URL?sumber=cronjob` pada menit ke-7 dan ke-37 tiap jam dengan header `Authorization: Bearer <CRON_SECRET>`. GitHub Actions (menit ke-7) menjadi cadangan karena jadwalnya sering terlambat/terlewat. Idempotency mencegah pembelian ganda; dashboard menghitung satu slot jadwal terlaksana bila ada panggilan dari penjadwal mana pun dalam 30 menit.

## Freeze & forward test

1. Isi `fee_*`, `forward_mulai` (waktu tutup candle 4 jam, ISO UTC), pastikan `pairs` final; jalankan backtest penuh.
2. `python tools/storage_admin.py reset --ya` (log uji pra-freeze diarsip di `logs/pra_freeze/`).
3. `git commit -am "FREEZE: aturan, parameter, universe"` → `git push`. Catat hash & waktu di atas.
4. Selama forward test jangan ubah `config/` atau `core/strategy.py`. Perbaikan bug lain: commit + `python tools/storage_admin.py catat-bugfix "..."`.

## Setelah forward test selesai

```bash
python tools/storage_admin.py export              # log mentah → logs/*.jsonl
python tools/fetch_history.py                     # candle terbaru
python backtest/run_backtest.py jendela           # → results/window/
curl -s https://<app>.vercel.app/api/report > report_vercel.json   # setelah results/window di-push & deploy
python hitung_ulang.py --banding report_vercel.json  # harus "0 berbeda"
```
Kumpulkan: ZIP repo (tanpa `.env`), `dist/dashboard_offline.html`, link Vercel, link repo.

## Keamanan
Secret hanya di Environment Variables Vercel / GitHub Secrets / `.env` lokal (di-.gitignore). `.env.example` tanpa isi.
`/api/tick` dan `/api/kill` menolak permintaan tanpa `Authorization: Bearer <CRON_SECRET>` (dan menolak bila secret < 16 karakter).

## Sumber resmi (isi tanggal akses)
- API publik Indodax: https://github.com/btcid/indodax-official-api-docs — akses ISI
- Rincian biaya transaksi: ISI URL help.indodax.com — akses ISI
- /api/pairs (`trade_fee_percent_taker`, `trade_min_base_currency`): https://indodax.com/api/pairs — akses ISI
- Vercel Cron, Functions, Storage: vercel.com/docs/cron-jobs/usage-and-pricing, /functions/limitations, /storage — akses ISI
