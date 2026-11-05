"""Eksekusi simulasi di order book: beli di ask, jual di bid, habiskan level demi level.
Fill di mid atau close TIDAK dipakai di mana pun (mid hanya dicatat untuk dekomposisi biaya)."""
import math


# Presisi kuantitas koin. /api/pairs mengembalikan volume_precision=0 untuk btcidr dkk. (dicek 5 Nov 2026),
# padahal koin diperdagangkan pecahan (mis. 0,0123 BTC). Field itu TIDAK dipakai sebagai presisi kuantitas;
# kuantitas dibulatkan ke bawah ke 8 desimal (satuan terkecil koin di order book Indodax).
QTY_DEC = 8


def floor_dec(x, d):
    f = 10 ** d
    return math.floor(x * f + 1e-9) / f


def book_ok(book, maks_spread):
    """Validasi order book. Return (ok, alasan, info)."""
    if not book or not book.get("asks") or not book.get("bids"):
        return False, "orderbook_kosong", {}
    bid, ask = book["bids"][0][0], book["asks"][0][0]
    if bid <= 0 or ask <= 0 or ask <= bid:
        return False, "orderbook_tidak_wajar", {"bid": bid, "ask": ask}
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid
    info = {"bid": bid, "ask": ask, "mid": mid, "spread": spread}
    if spread > maks_spread:
        return False, "spread_lebih_2persen", info
    return True, None, info


def walk_buy_idr(asks, idr, qty_dec):
    """Beli senilai `idr` (sebelum fee) dengan menghabiskan level ask berurutan.
    Kuantitas dibulatkan ke bawah sesuai presisi pasangan. Return dict fill."""
    qty_raw, sisa = 0.0, idr
    for price, amt in asks:
        if sisa <= 0:
            break
        take = min(amt, sisa / price)
        qty_raw += take
        sisa -= take * price
    qty = floor_dec(qty_raw, qty_dec)
    return walk_qty(asks, qty, partial_reason="kedalaman_ask_kurang" if sisa > 1e-6 else None)


def walk_qty(levels, qty, partial_reason=None):
    """Isi kuantitas `qty` dari level berurutan (ask naik untuk beli, bid turun untuk jual)."""
    need, cost, used = qty, 0.0, 0
    for price, amt in levels:
        if need <= 1e-15:
            break
        take = min(amt, need)
        cost += take * price
        need -= take
        used += 1
    filled = qty - max(0.0, need)
    reason = partial_reason
    if need > 1e-12:
        reason = "kedalaman_kurang"
    return {
        "qty": filled,
        "avg": (cost / filled) if filled > 0 else None,
        "notional": cost,
        "levels": used,
        "parsial": reason,
    }


def fee_resmi(cfg, sisi):
    return cfg["fee_taker_beli_persen_resmi"] if sisi == "buy" else cfg["fee_taker_jual_persen_resmi"]


def fee_rate(cfg, api_fee_pct, sisi):
    """Tarif taker per sisi (pajak hanya saat jual): pakai yang lebih mahal antara
    halaman resmi (config) dan /api/pairs (live)."""
    vals = [fee_resmi(cfg, sisi)]
    if isinstance(api_fee_pct, (int, float)):
        vals.append(api_fee_pct)
    return max(vals) / 100.0


def cost_decomposition(side, qty, avg, info, signal_price):
    """Pecah selisih harga fill vs harga sinyal (close candle) menjadi:
    tunda (mid saat eksekusi - close sinyal), spread (best - mid), slippage (avg - best).
    Untuk beli: fill - sinyal = tunda + spread + slippage.
    Untuk jual: sinyal - fill = -tunda + spread + slippage  (biaya positif = merugikan)."""
    mid, bid, ask = info["mid"], info["bid"], info["ask"]
    if side == "buy":
        return {
            "tunda_idr": qty * (mid - signal_price),
            "spread_idr": qty * (ask - mid),
            "slippage_idr": qty * (avg - ask),
        }
    return {
        "tunda_idr": qty * (signal_price - mid),
        "spread_idr": qty * (mid - bid),
        "slippage_idr": qty * (bid - avg),
    }
