# Refleksi: backtest vs forward test

Fatimah Tri Lestari · 25110200019 · UAS Algoritma Trading

Semua angka di bawah berasal dari `dist/report.json` yang dihasilkan `python hitung_ulang.py` dari log mentah
(`logs/*.jsonl`) dan hasil backtest (`results/`). Nama kunci JSON ditulis dalam kurung agar bisa dicek ulang.
Angka ini adalah angka akhir setelah forward test selesai (14 November 2026 12.00 WIB).

## 1. Ringkasan

| | Backtest 3 tahun | Backtest jendela forward | Forward test | BTC/IDR beli dan tahan (forward) |
|---|---|---|---|---|
| Return total | +62,01% | 0,00% | 0,00% | −16,22% |
| CAGR | +17,45% | tidak dihitung | tidak dihitung | tidak dihitung |
| Max drawdown | −19,22% | 0,00% | 0,00% | −16,22% |
| Jumlah trade | 50 | 0 | 0 | |
| Win rate / profit factor | 42,0% / 1,95 | tidak ada trade | tidak ada trade | |
| Porsi biaya | 19,0% | tidak ada trade | tidak ada trade | |

(`backtest.*`, `gap.backtest_jendela.*`, `forward.*`, `forward.benchmark_btc.*`)

Sebagai pembanding periode backtest, BTC/IDR beli dan tahan menghasilkan +157,66% (CAGR +37,09%) dengan drawdown −49,43%.
Strategi menghasilkan return lebih rendah, tetapi drawdown-nya kurang dari setengah drawdown BTC.

## 2. Kecocokan sinyal backtest dan forward

- Backtest dijalankan ulang pada jendela forward test (`python backtest/run_backtest.py jendela`) dengan fungsi
  sinyal yang sama (`core/strategy.py`).
- **Sinyal identik 210 dari 210 pasangan per candle (100%)** (`gap.sinyal.persen_identik`). 210 = 42 candle × 5 pasangan
  yang datanya diterima bot; tidak ada sinyal yang hanya muncul di backtest atau hanya di bot (`hanya_backtest` = 0, `hanya_bot` = 0).
- 12 candle (60 baris sinyal) tidak dibandingkan karena bot tidak mendapat data dari Indodax (HTTP 403, lihat bagian 5)
  (`gap.sinyal.bot_tanpa_data`).
- Backtest jendela juga **0 pembelian**, termasuk pada 12 candle yang terganggu. Artinya gangguan tersebut tidak membuat
  bot kehilangan satu pun trade, dan selisih return forward dengan backtest jendela adalah 0,00% (`gap.selisih_return`).

## 3. Mengapa tidak ada trade

Dari 270 baris sinyal (54 candle × 5 pasangan) selama forward test (`forward.kondisi_terpenuhi`):

| Syarat masuk | Terpenuhi |
|---|---|
| Filter rezim: BTC/IDR di atas SMA100 | 5 kali (hanya satu candle) |
| Breakout di atas high 55 candle | 0 kali |
| Close di atas SMA100 | 5 kali |
| SMA100 naik > 2% dalam 20 candle | 0 kali |
| Volatilitas menyempit | 12 kali |
| Semua syarat sekaligus | 0 kali |

Selama forward test BTC/IDR turun 16,22%. Filter rezim dan syarat tren menahan bot agar tidak membeli di pasar
yang sedang turun. Hasil 0 trade adalah konsekuensi aturan yang dibekukan, bukan kerusakan bot, dan dibuktikan oleh
backtest jendela yang juga tidak membeli.

## 4. Selisih harga fill per trade

Tidak ada order selama forward test, sehingga tidak ada selisih fill yang bisa diukur. Bila ada trade, selisih harga fill
bot terhadap harga sinyal dipecah menjadi tiga komponen yang dicatat per order (`orders.jsonl`):
keterlambatan jadwal (mid saat eksekusi dikurangi close sinyal), setengah spread, dan slippage karena menghabiskan level
order book.

Keterlambatan jadwal yang terukur (`operasional`):
- Candle tutup sampai diproses: median 7,1 menit, maksimum 7,2 menit (`tunda_proses_detik`). Bot dijadwalkan menit ke-7.
- Keterlambatan dari jadwal cron: median 6,9 detik, maksimum 9,5 detik (`keterlambatan_jadwal_detik`).
- Durasi satu panggilan: median 4,9 detik, maksimum 41,8 detik (`durasi_panggilan_detik`); maksimum terjadi saat
  request ditolak dan dicoba ulang.

Backtest mengasumsikan eksekusi di close candle, sehingga keterlambatan sekitar 7 menit ini adalah sumber selisih harga
yang tidak dimodelkan backtest.

## 5. Gangguan dan perbaikan selama forward test

**HTTP 403 dari Indodax.** Sejak sekitar 7 November, Indodax menolak request bot dengan HTTP 403. Penyebabnya ditemukan
pada 9 November: Indodax menolak User-Agent lama (`uas-dryrun-bot/1.0`) dan `Python-urllib`. Uji curl dari laptop:
User-Agent lama 403, curl biasa 200. User-Agent diganti menjadi `UAS-AlgoTrading-25110200019/1.0 (tugas kuliah)`
(commit `a39aa15`), tanpa menyamar sebagai browser.
- 108 panggilan menerima 403, 3.888 baris error (`panggilan_dengan_403`, `error_api_total`).
- Ditemukan juga bug: candle tanpa data tetap ditandai sudah diproses. Diperbaiki agar candle dicoba lagi pada panggilan
  berikutnya, dan uptime dihitung jujur (commit `d1d6fef`).
- Region fungsi Vercel sempat dipindah ke `sin1` sebelum penyebab sebenarnya diketahui (commit `06e4c22`).

**Uptime.** 42 dari 54 candle diproses tepat waktu = **77,78%** (`operasional.uptime`). Ke-12 candle yang tidak
diproses semuanya jatuh pada periode HTTP 403 (`candle_tanpa_data`). Di luar 12 candle gangguan Indodax itu, uptime
**100%** (42/42, `uptime_di_luar_gangguan_indodax`). Gangguan ini berasal dari Indodax dan tercatat lengkap di log.

**Penjadwal.** GitHub Actions ternyata jarang berjalan sesuai jadwal (35 panggilan selama forward test), sehingga
cron-job.org dipakai sebagai penjadwal utama pada menit ke-7 dan ke-37 (426 panggilan); 9 panggilan lain dari cron
harian Vercel (`panggilan_per_sumber`). Slot jadwal terlaksana 426 dari 426 (`panggilan_terjadwal_terlaksana`),
total 470 panggilan.

**Kill switch.** Diuji 9 Nov pukul 21.20 WIB dan tidak sengaja baru dimatikan 10 Nov pukul 07.42 WIB (sekitar 10 jam).
Selama itu 3 candle diproses dan 0 sinyal beli terblokir (`signals.jsonl`, aksi `tolak:blokir_risiko` tidak ada).
Setiap perubahan tercatat di `events.jsonl`.

**Daily loss limit.** Tidak terpicu karena tidak ada posisi. Fungsinya dibuktikan oleh uji otomatis
`test_daily_loss_limit_memblokir_posisi_baru`.

Semua perbaikan setelah freeze tidak mengubah `config/` maupun `core/strategy.py`; hash parameter `0e2cb6f315d4dbef`
sama di seluruh log (`param_hash_di_log`).

## 6. Spread: asumsi backtest dibanding seluruh snapshot

Setengah spread median dalam basis poin (`spread.asumsi_backtest`, `spread.seluruh_snapshot`):

| Pasangan | Asumsi backtest (34 snapshot sampai freeze) | Seluruh snapshot bot (372 valid per pasangan) | Spread p90 |
|---|---|---|---|
| BTC/IDR | 1,3 | 1,0 | 12,1 |
| ETH/IDR | 1,1 | 3,6 | 15,0 |
| XRP/IDR | 2,6 | 3,4 | 15,7 |
| DOGE/IDR | 7,1 | 7,6 | 23,1 |
| SOL/IDR | 5,8 | 5,0 | 17,1 |

Asumsi backtest berasal dari snapshot yang dikumpulkan hanya sekitar 10 jam sebelum freeze. Median selama forward test
lebih lebar untuk ETH/IDR (lebih dari tiga kali) dan XRP/IDR, dan sedikit lebih sempit untuk BTC/IDR dan SOL/IDR. Spread p90
jauh di atas median, artinya pada jam tertentu biaya eksekusi bisa beberapa kali lebih mahal daripada yang diasumsikan.
Dampaknya pada backtest 3 tahun: porsi biaya 19,0% bisa lebih tinggi untuk ETH dan XRP.

## 7. Keterbatasan dan catatan jujur

- **Freeze terlambat.** Jadwal freeze Rabu 4 Nov 12.00 WIB; freeze dilakukan Kamis 5 Nov 14.44 WIB (commit `18c7780`),
  sehingga forward test berjalan sekitar 8,9 hari, bukan 10 hari. Pada commit freeze `18c7780`, aturan, parameter,
  universe, dan waktu mulai forward test tercatat di `config/strategy.json` dan `STRATEGI.md`. Ringkasannya di README
  (hash commit freeze dan jadwal forward test) baru ditambahkan setelah freeze, tanpa mengubah aturan.
- **Overfitting.** Strategi berasal dari UTS (saham harian) yang dipilih dari 947 kombinasi. Parameter sengaja tidak
  dioptimasi ulang untuk kripto.
- **Survivorship bias.** Universe dipilih dari likuiditas 30 hari sebelum backtest mulai, tetapi kandidat diambil dari
  daftar pasangan yang masih ada saat ini.
- **Fee.** Dipakai yang lebih mahal per sisi: beli 0,20555%, jual 0,41555% (halaman resmi Indodax, diakses 5 Nov 2026),
  dibanding 0,2% di `/api/pairs`.
- **Penggunaan AI.** AI dipakai untuk membantu menyusun kode bot, backtest, dashboard, dan dokumentasi. Seluruh angka
  berasal dari data Indodax dan log bot, dan dapat dihitung ulang dengan `hitung_ulang.py`.
