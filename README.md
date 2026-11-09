# Bot Dry Run Indodax — UAS Algoritma Trading

Fatimah Tri Lestari · 25110200019

Bot long-only yang membaca harga dan order book **asli** Indodax (API publik, tanpa API key), mengeksekusi order
**simulasi** di bid/ask, dan mencatat setiap keputusan. Semua angka dashboard dihitung oleh `core/report.py` dari log mentah;
`hitung_ulang.py` menghasilkan ulang angka yang sama secara offline.

- Link Vercel (dashboard live): https://uas-bot-indodax.vercel.app
- Link repositori: https://github.com/fatimahtriiils/uas-bot-indodax
- Commit freeze: `18c7780d08b0892e1222913459121fc25a58aaf5`, 5 November 2026 14.44.12 WIB, sudah di-push sebelum forward test dimulai
- Strategi & catatan perubahan dari UTS: [STRATEGI.md](STRATEGI.md)
- Refleksi: [REFLEKSI.md](REFLEKSI.md)

## Freeze dan forward test

| | |
|---|---|
| Aturan, parameter, universe | `config/strategy.json` pada commit freeze (hash parameter `0e2cb6f315d4dbef`, tercatat di setiap baris log `calls`) |
| Universe | BTC/IDR, ETH/IDR, XRP/IDR, DOGE/IDR, SOL/IDR; bukti kriteria di `config/universe_bukti.json` |
| Forward test mulai | 5 November 2026 15.00 WIB (`forward_mulai` = 2026-11-05T08:00:00Z, waktu tutup candle 4 jam) |
| Forward test selesai | 14 November 2026 12.00 WIB (`forward_selesai` = 2026-11-14T05:00:00Z) |
| Penjadwal | cron-job.org menit ke-7 dan ke-37 tiap jam (utama), GitHub Actions menit ke-7 (cadangan) |

**Keterlambatan freeze.** Jadwal soal menetapkan freeze Rabu 4 November 2026 12.00 WIB. Freeze baru dilakukan
Kamis 5 November 2026 14.44 WIB (commit di atas), sehingga forward test berjalan sekitar 8,9 hari, bukan 10 hari.
Waktu sebenarnya dicatat apa adanya; tidak ada riwayat commit yang ditulis ulang.

**Perubahan setelah freeze** (tidak mengubah aturan, parameter, maupun universe; dicatat di log `events` dengan jenis `perbaikan_bug`):
- Tampilan dashboard dan panel "sinyal terakhir" (`public/index.html`, `core/report.py`).
- 9 Nov 2026, perbaikan bug: pada sebagian panggilan Indodax menolak semua request dari Vercel (HTTP 403). Sebelumnya bot
  tetap menandai candle itu sebagai sudah diproses tanpa data, sehingga panggilan berikutnya tidak mencoba lagi. Sekarang
  candle tidak ditandai selesai bila data gagal diambil (status `data_gagal_dicoba_lagi`), dan dashboard tidak lagi
  menghitung candle tanpa data sebagai tepat waktu (`core/bot.py`, `core/report.py`). Aturan trading tidak berubah.

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
tests/test_bot.py        uji paritas sinyal bot vs backtest, idempotency, kill switch, daily loss limit, retry, order book
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

## Eksekusi: fee, minimum order, pembulatan

| Sumber | Nilai | URL | Diakses |
|---|---|---|---|
| Halaman resmi (IDR Market PRO, taker) | beli 0,20555% (service 0,2% + pajak 0% + bourse 0,00555%); jual 0,41555% (service 0,2% + pajak 0,21% + bourse 0,00555%) | https://indodax.com/profile, bagian Trading Fee (perlu login; screenshot `docs/fee_indodax_2026-11-05.png`) | 5 Nov 2026 |
| `/api/pairs` | `trade_fee_percent_taker` = 0,2; `trade_min_base_currency` = 10000 | https://indodax.com/api/pairs | 5 Nov 2026 |

- Fee yang dipakai per sisi = yang lebih mahal: **beli 0,20555%, jual 0,41555%**.
- Minimum order: Rp25.000 (lebih ketat daripada Rp10.000 di `/api/pairs`).
- Harga fill diambil langsung dari level order book (beli di ask, jual di bid, menghabiskan level), sehingga selalu
  mengikuti kenaikan harga yang berlaku. Bot tidak memasang limit order.
- Kuantitas dibulatkan ke bawah ke 8 desimal. `/api/pairs` mengembalikan `volume_precision` = 0 untuk semua pasangan,
  padahal koin diperdagangkan pecahan, sehingga field itu tidak dipakai (lihat `core/execution.py`).

## Sumber resmi
- API publik Indodax: https://github.com/btcid/indodax-official-api-docs (ISI tanggal akses)
- Vercel Cron, Functions, Storage: https://vercel.com/docs/cron-jobs/usage-and-pricing, https://vercel.com/docs/functions/limitations, https://vercel.com/docs/storage (ISI tanggal akses)
