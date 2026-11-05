"""FUNGSI SINYAL BERSAMA — dipakai persis sama oleh bot (api/tick.py) dan backtest.

Semua indikator memakai jendela tetap (rata-rata sederhana), bukan smoothing
rekursif. Akibatnya nilai indikator hanya bergantung pada bar di jendela itu,
sehingga bot (yang hanya mengambil ~300 bar terakhir) dan backtest (yang
memegang 3 tahun data) menghasilkan angka yang identik untuk candle yang sama.

Bar = dict {t, o, h, l, c, v}; t = waktu BUKA candle (unix detik, UTC).
Sinyal di bar i hanya memakai bar[0..i] yang sudah TERTUTUP.
"""


def lookback_bars(p):
    """Jumlah bar minimum (termasuk bar sinyal) agar semua indikator terdefinisi."""
    return max(
        p["breakout_n"] + 1,
        p["sma_tren"] + p["slope_lookback"],
        p["atr_len"] + p["atr_rasio_len"],
    ) + 1


def _mean(xs):
    return sum(xs) / len(xs)


def _tr(bars, k):
    b, pc = bars[k], bars[k - 1]["c"]
    return max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))


def _atr(bars, k, n):
    return _mean([_tr(bars, j) for j in range(k - n + 1, k + 1)])


def window_check(bars, i, p):
    """Validasi data di jendela indikator (aturan yang sama di bot & backtest).
    Mengembalikan None bila valid, atau string alasan bila tidak valid."""
    tf = p["timeframe_menit"] * 60
    L = lookback_bars(p)
    if i + 1 < L:
        return "data_kurang"
    w = bars[i - L + 1: i + 1]
    for a, b in zip(w, w[1:]):
        if b["t"] - a["t"] != tf:
            return "candle_hilang"
    for b in w:
        if min(b["o"], b["h"], b["l"], b["c"]) <= 0:
            return "harga_nol"
    if bars[i]["v"] <= 0:
        return "volume_nol"
    return None


def evaluate(bars, i, p):
    """Indikator & kondisi masuk pada bar i. Murni fungsi dari bars[..i]."""
    bad = window_check(bars, i, p)
    if bad:
        return {"t": bars[i]["t"] if i < len(bars) else None, "valid": False, "alasan": bad, "entry": False}
    N, T, S = p["breakout_n"], p["sma_tren"], p["slope_lookback"]
    c = bars[i]["c"]
    hh = max(b["h"] for b in bars[i - N: i])            # high tertinggi N bar SEBELUM bar sinyal
    sma_now = _mean([b["c"] for b in bars[i - T + 1: i + 1]])
    sma_prev = _mean([b["c"] for b in bars[i - S - T + 1: i - S + 1]])
    slope = sma_now / sma_prev - 1
    A, R = p["atr_len"], p["atr_rasio_len"]
    atrp = [_atr(bars, k, A) / bars[k]["c"] for k in range(i - R + 1, i + 1)]
    atr_now = _atr(bars, i, A)
    atrp_now, atrp_mean = atrp[-1], _mean(atrp)
    cond = {
        "breakout": c > hh,
        "di_atas_sma": c > sma_now,
        "slope_ok": slope > p["slope_min"],
        "vol_menyempit": atrp_now < p["atr_rasio_max"] * atrp_mean,
    }
    return {
        "t": bars[i]["t"], "valid": True, "alasan": None,
        "close": c, "level": hh, "sma": sma_now, "slope": slope,
        "atr": atr_now, "atrp": atrp_now, "atrp_mean": atrp_mean,
        "kekuatan": c / hh, **cond,
        "entry_raw": all(cond.values()),
    }


def regime_ok(btc_bars, j, p):
    """Filter rezim (pengganti filter IHSG di UTS): close BTC/IDR > SMA tren BTC/IDR."""
    T = p["sma_tren"]
    if j is None or j + 1 < T:
        return False
    w = btc_bars[j - T + 1: j + 1]
    tf = p["timeframe_menit"] * 60
    if any(b["t"] - a["t"] != tf for a, b in zip(w, w[1:])):
        return False
    return btc_bars[j]["c"] > _mean([b["c"] for b in w])


def exit_signal(pos, ev_bar, p):
    """Sinyal keluar untuk posisi terbuka, dievaluasi pada close bar.
    Memakai harga close bar sinyal masuk (bukan harga fill) supaya identik
    antara bot dan backtest. Return (bool, alasan)."""
    tf = p["timeframe_menit"] * 60
    held = (ev_bar["t"] - pos["sinyal_t"]) // tf
    stop = pos["sinyal_close"] - p["stop_atr_mult"] * pos["sinyal_atr"]
    if ev_bar["c"] <= stop:
        return True, "stop_atr"
    if held >= p["hold_bars"]:
        return True, f"waktu_{p['hold_bars']}_bar"
    return False, None


def plan(positions, evals, bars_now, p, boleh_buka, regime):
    """Keputusan portofolio untuk satu candle (bersama untuk bot & backtest).

    positions : {pair: pos}
    evals     : {pair: hasil evaluate() untuk candle ini}
    bars_now  : {pair: bar candle ini} (None jika tidak ada)
    boleh_buka: False bila kill switch / daily loss limit aktif
    Return dict: exits [(pair, alasan)], entries [pair], ditolak {pair: alasan}
    """
    exits, ditolak = [], {}
    for pair in sorted(positions):
        b = bars_now.get(pair)
        if b is None:
            continue
        ok, why = exit_signal(positions[pair], b, p)
        if ok:
            exits.append((pair, why))
    sisa = len(positions) - len(exits)
    slot = max(0, p["maks_posisi"] - sisa)
    kandidat = []
    for pair in sorted(evals):
        e = evals[pair]
        if not (e.get("valid") and e.get("entry_raw")):
            continue
        if pair in positions:
            ditolak[pair] = "sudah_pegang"
            continue
        if not regime:
            ditolak[pair] = "rezim_btc_off"
            continue
        if not boleh_buka:
            ditolak[pair] = "blokir_risiko"
            continue
        kandidat.append(pair)
    kandidat.sort(key=lambda x: (-evals[x]["kekuatan"], x))
    entries = kandidat[:slot]
    for pair in kandidat[slot:]:
        ditolak[pair] = "slot_penuh"
    return {"exits": exits, "entries": entries, "ditolak": ditolak}


def entry_final(ev, regime):
    """Sinyal masuk final (sebelum batasan slot/risiko) yang dibandingkan candle-demi-candle."""
    return bool(ev.get("valid") and ev.get("entry_raw") and regime)


def target_size(equity, cash, p):
    """Ukuran order beli (IDR, sebelum fee)."""
    target = min(equity / p["maks_posisi"], equity * p["maks_porsi_per_posisi"])
    return max(0.0, min(target, cash))
