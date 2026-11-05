"""Memuat konfigurasi strategi (satu sumber untuk bot dan backtest)."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "strategy.json")
COSTS_PATH = os.path.join(ROOT, "config", "costs.json")

# Parameter yang menentukan sinyal. Hash-nya dicatat di setiap log agar
# perubahan setelah freeze langsung terlihat.
PARAM_KEYS = [
    "versi", "timeframe_menit", "pairs", "pair_rezim", "breakout_n", "sma_tren",
    "slope_lookback", "slope_min", "atr_len", "atr_rasio_len", "atr_rasio_max",
    "hold_bars", "stop_atr_mult", "maks_posisi", "maks_porsi_per_posisi",
    "daily_loss_limit", "modal_awal", "min_order_idr", "maks_spread",
    "maks_umur_candle_x_tf", "fee_taker_beli_persen_resmi", "fee_taker_jual_persen_resmi",
]


class ConfigError(Exception):
    pass


def load_config(path=CONFIG_PATH, require_fee=True):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in PARAM_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"Kunci konfigurasi hilang: {missing}")
    if require_fee and not all(isinstance(cfg.get(k), (int, float)) for k in ("fee_taker_beli_persen_resmi", "fee_taker_jual_persen_resmi")):
        raise ConfigError(
            "fee_taker_beli_persen_resmi / fee_taker_jual_persen_resmi belum diisi. Isi dari halaman resmi Rincian Biaya "
            "Transaksi Indodax (tarif taker all-in termasuk pajak, efektif 1 Okt 2026), "
            "lengkap dengan URL dan tanggal akses.")
    if not (3 <= len(cfg["pairs"]) <= 10):
        raise ConfigError("Universe harus 3-10 pasangan.")
    return cfg


def param_hash(cfg):
    sub = {k: cfg[k] for k in PARAM_KEYS}
    raw = json.dumps(sub, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def load_costs(path=COSTS_PATH):
    """Biaya spread/slippage hasil pengukuran snapshot (dibuat tools/cost_model.py)."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
