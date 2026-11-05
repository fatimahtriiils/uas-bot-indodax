# Refleksi: backtest vs forward test

Isi setelah forward test selesai. **Setiap angka di sini disalin dari keluaran `python hitung_ulang.py`
(atau `dist/report.json`), sebutkan kunci JSON-nya**, supaya bisa dicek ulang.

## 1. Kecocokan sinyal
- Sinyal identik: ISI / ISI pasangan×candle (`gap.sinyal.persen_identik`).
- Candle yang tidak diproses bot: ISI (`operasional.candle_tidak_diproses`) — penyebab: ISI (lihat `errors`, `calls`).
- Bila ada perbedaan: candle mana, nilai close bot vs backtest, dan penyebabnya (revisi data Indodax, candle hilang, order book tidak valid).

## 2. Selisih harga fill per trade
Rata-rata (bps terhadap harga sinyal, `gap.fill_rata2_bps`):
| Komponen | Bot | Asumsi backtest |
|---|---|---|
| Keterlambatan jadwal | ISI | 0 |
| Setengah spread | ISI | ISI |
| Slippage | ISI | ISI |

Jelaskan trade dengan selisih terbesar (tabel "Per order").

## 3. Spread asumsi vs seluruh snapshot
Bandingkan `spread.asumsi_backtest` dengan `spread.seluruh_snapshot` per pasangan. Snapshot asumsi berjumlah ISI (dikumpulkan sejak ISI sampai freeze),
seluruh snapshot ISI.

## 4. Return & drawdown
Forward ISI vs backtest jendela ISI (`gap.selisih_return`). Uraikan selisihnya: biaya eksekusi aktual, keterlambatan, daily loss limit, kill switch, candle terlewat.

## 5. Operasional
Uptime ISI (`operasional.uptime`); panggilan terjadwal ISI vs terlaksana ISI; median keterlambatan GitHub Actions ISI menit; error API ISI.
Dampak keterlambatan terhadap harga fill: ISI.

## 6. Catatan jujur
- Freeze dilakukan ISI (terlambat dari jadwal 4 Nov 12.00 WIB) — ISI penjelasan & konfirmasi dosen.
- Snapshot untuk asumsi biaya backtest hanya ISI jam sebelum freeze.
- Survivorship: kandidat universe dari /api/pairs saat ini.
