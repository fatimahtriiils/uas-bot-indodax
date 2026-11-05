# Strategi tertulis — Struktur Breakout + Keluar Waktu (versi kripto 4 jam)

Fatimah Tri Lestari · 25110200019 · UAS Algoritma Trading

Semua angka parameter berasal dari `config/strategy.json`, satu-satunya file yang dibaca bot dan backtest.
Fungsi sinyal ada di `core/strategy.py` dan dipanggil oleh **keduanya**.

## 1. Aturan lengkap

**Timeframe.** Candle 4 jam (`timeframe_menit = 240`). Sinyal hanya dihitung dari candle yang sudah tertutup
(bar dengan `Time + 4 jam > server_time` dibuang).

**Universe.** `pairs` di config (3–10 pasangan IDR), dipilih dengan `tools/select_universe.py`:
median nilai transaksi harian pada 30 hari **sebelum** `backtest_mulai`, stablecoin dikecualikan, maksimal 2 hari tanpa
transaksi. Bukti peringkat tersimpan di `config/universe_bukti.json`.

**Filter rezim.** Posisi baru hanya dibuka bila close BTC/IDR > SMA(`sma_tren`) BTC/IDR pada candle yang sama.

**Aturan masuk** (semua harus benar pada candle sinyal *i*):
1. Breakout: close[i] > high tertinggi `breakout_n` candle sebelumnya (tidak termasuk candle *i*).
2. Tren: close[i] > SMA(`sma_tren`).
3. Kemiringan: SMA(`sma_tren`)[i] / SMA(`sma_tren`)[i − `slope_lookback`] − 1 > `slope_min`.
4. Volatilitas menyempit: ATR(`atr_len`)/close < `atr_rasio_max` × rata-rata ATR/close `atr_rasio_len` candle terakhir.
   ATR = rata-rata sederhana true range (bukan Wilder), supaya nilainya hanya bergantung pada jendela tetap.
5. Data valid (lihat bagian 3) dan pasangan belum dipegang.

Bila kandidat lebih banyak dari slot kosong, urutkan berdasarkan close/level breakout terbesar (seri: nama pasangan).

**Aturan keluar** (dievaluasi pada close setiap candle, keluar dulu baru masuk):
1. Stop ATR: close ≤ close candle sinyal − `stop_atr_mult` × ATR candle sinyal.
2. Waktu: setelah `hold_bars` candle sejak candle sinyal.
Acuan stop memakai close candle sinyal, bukan harga fill, agar sinyal bot dan backtest identik.

**Sizing.** Nilai beli = min(equity / `maks_posisi`, `maks_porsi_per_posisi` × equity, kas), sebelum fee.
Kuantitas dibulatkan ke bawah sesuai `volume_precision` pasangan. Order di bawah Rp25.000 (atau minimum API bila lebih besar) dibatalkan.

**Eksekusi dry run.** Beli di ask, jual di bid, menghabiskan level order book berurutan (harga rata-rata).
Fee taker per sisi = maks(tarif halaman resmi, `trade_fee_percent_taker` dari /api/pairs). Halaman resmi (IDR Market PRO, akses 5 Nov 2026): **beli 0,20555%** (service 0,2% + pajak 0% + bourse 0,00555%), **jual 0,41555%** (service 0,2% + pajak 0,21% + bourse 0,00555%); /api/pairs: 0,2%. Jadi dipakai 0,20555% (beli) dan 0,41555% (jual). Tidak ada fill di mid atau close.

## 2. Risk management (di kode `core/bot.py`)
- Maksimal `maks_posisi` posisi; maksimal `maks_porsi_per_posisi` equity per posisi.
- Daily loss limit `daily_loss_limit` dari equity awal hari WIB (00.00 WIB = 17.00 UTC) → tidak membuka posisi baru sampai hari berikutnya. Keluar tetap berjalan.
- Kill switch: flag `uas:kill_switch` di Redis, diubah lewat `/api/kill` atau `tools/storage_admin.py kill on|off` tanpa deploy ulang. Setiap perubahan tercatat di log `events`.
- Timeout 8 detik, maksimal 3 percobaan per request dengan backoff 1–2 detik, jeda ≥0,35 detik; batas waktu total 200 detik.
- Idempotency: penanda `uas:candle:<t>` (SET NX) + `last_candle_t` di state + kunci `uas:lock`; candle yang sama tidak bisa memicu pembelian dua kali.

## 3. Validasi data (bot tidak membuka posisi, alasan dicatat)
Candle tertutup terakhir > 2× timeframe (`candle_basi`), candle hilang di jendela indikator, harga/volume nol,
order book kosong/tidak wajar, selisih bid–ask > 2%.

## 4. Catatan perubahan dari versi UTS

| Aspek | UTS (saham BEI) | UAS (kripto Indodax) | Alasan |
|---|---|---|---|
| Pasar | 100 saham Kompas100 | 3–10 pasangan IDR likuid | ketentuan UAS |
| Timeframe | harian | 4 jam | cukup candle dalam 10 hari forward test; penjadwal per jam memberi 4 kesempatan per candle |
| Filter rezim | IHSG > SMA100 | BTC/IDR > SMA100 | tidak ada IHSG di kripto |
| Lookback (55, 100, 20, 14, 60) | dalam hari | dalam candle 4 jam | angka dipertahankan, satuan berubah; tidak dioptimasi ulang |
| Keluar waktu | 30 hari bursa | `hold_bars` candle | |
| Stop-loss | tidak ada (trade terburuk ≈ −40%) | stop `stop_atr_mult` × ATR | kelemahan utama UTS; volatilitas kripto lebih tinggi |
| Slot | 10 posisi | `maks_posisi` | universe lebih kecil |
| ATR | (versi UTS) | rata-rata sederhana | identik antara bot (300 bar) dan backtest (3 tahun) |
| Eksekusi | open hari berikutnya, fee BEI + 1 tick | ask/bid order book; backtest: close ± spread & slippage terukur | ketentuan UAS |
| Risk | — | daily loss limit, kill switch, idempotency, retry | ketentuan UAS |

Parameter tidak dioptimasi ulang untuk kripto. Ini disengaja: UTS sudah mencoba 947 kombinasi, sehingga
optimasi ulang hanya menambah risiko overfitting.

## 5. Freeze
- Waktu freeze & mulai forward test: `forward_mulai` di config. Hash commit freeze: ISI setelah commit.
- Hash parameter (`core/config.param_hash`) dicatat di setiap baris log `calls` dan `signals`.
