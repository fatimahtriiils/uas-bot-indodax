"""Klien API publik Indodax (tanpa API key) dengan timeout, retry terbatas, dan jeda.
Hanya memakai pustaka standar Python agar fungsi Vercel ringan dan stabil."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://indodax.com"
UA = "UAS-AlgoTrading-25110200019/1.0 (tugas kuliah)"


class Indodax:
    def __init__(self, timeout=8, retries=3, min_interval=0.35, budget_s=None, sleeper=time.sleep, opener=None):
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval        # <= 3 request/detik, tidak paralel
        self.sleep = sleeper
        self.opener = opener or self._open
        self.errors = []                        # setiap kegagalan dicatat (masuk log errors)
        self.n_requests = 0
        self._last = 0.0
        self._t0 = time.time()
        self.budget_s = budget_s                # batas waktu total (fungsi Vercel <= 300 dtk)

    def _open(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, r.read()

    def get_json(self, path, params=None, konteks=""):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(1, self.retries + 1):
            if self.budget_s and time.time() - self._t0 > self.budget_s:
                self.errors.append({"url": url, "konteks": konteks, "percobaan": attempt, "jenis": "budget_waktu_habis"})
                return None
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.time()
            self.n_requests += 1
            jenis, detail = None, None
            try:
                status, body = self.opener(url)
                if status != 200:
                    jenis, detail = "http_" + str(status), None
                elif not body or not body.strip():
                    jenis = "respons_kosong"
                else:
                    data = json.loads(body)
                    if data in (None, [], {}) or (isinstance(data, dict) and data.get("error")):
                        jenis, detail = "respons_kosong_atau_error", str(data)[:200]
                    else:
                        return data
            except urllib.error.HTTPError as e:
                jenis, detail = f"http_{e.code}", str(e)[:200]
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                jenis, detail = "timeout_atau_jaringan", str(e)[:200]
            except ValueError as e:
                jenis, detail = "json_tidak_valid", str(e)[:200]
            self.errors.append({"url": url, "konteks": konteks, "percobaan": attempt, "jenis": jenis, "detail": detail})
            if attempt < self.retries:
                self.sleep(min(4.0, 1.0 * 2 ** (attempt - 1)))   # backoff 1, 2 detik
        return None

    # ---------------------------------------------------------------- endpoint
    def server_time(self):
        d = self.get_json("/api/server_time", konteks="server_time")
        if not d:
            return None
        v = d.get("server_time") if isinstance(d, dict) else None
        if v is None:
            return None
        v = float(v)
        return v / 1000.0 if v > 1e12 else v

    def candles(self, pair, tf_menit, frm, to):
        tf = "1D" if tf_menit == 1440 else str(tf_menit)
        d = self.get_json("/tradingview/history_v2",
                          {"symbol": pair.upper(), "tf": tf, "from": int(frm), "to": int(to)},
                          konteks=f"candles:{pair}")
        return parse_candles(d) if d is not None else None

    def depth(self, pair):
        d = self.get_json(f"/api/depth/{pair}", konteks=f"depth:{pair}")
        return parse_depth(d) if d is not None else None

    def pairs(self):
        d = self.get_json("/api/pairs", konteks="pairs")
        return parse_pairs(d) if d is not None else None

    def ticker_all(self):
        return self.get_json("/api/ticker_all", konteks="ticker_all")

    def price_increments(self):
        return self.get_json("/api/price_increments", konteks="price_increments")


# -------------------------------------------------------------------- parser
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def parse_candles(d):
    """Mendukung format list-of-dict {Time,Open,High,Low,Close,Volume} dan format
    TradingView {t:[],o:[],h:[],l:[],c:[],v:[]}. Waktu dinormalkan ke detik."""
    rows = []
    if isinstance(d, dict) and "t" in d and isinstance(d["t"], list):
        for k in range(len(d["t"])):
            rows.append({"t": d["t"][k], "o": d["o"][k], "h": d["h"][k], "l": d["l"][k],
                         "c": d["c"][k], "v": (d.get("v") or [0] * len(d["t"]))[k]})
    elif isinstance(d, list):
        for r in d:
            if isinstance(r, dict):
                g = {k.lower(): v for k, v in r.items()}
                rows.append({"t": g.get("time", g.get("t")), "o": g.get("open", g.get("o")),
                             "h": g.get("high", g.get("h")), "l": g.get("low", g.get("l")),
                             "c": g.get("close", g.get("c")), "v": g.get("volume", g.get("v", 0))})
            elif isinstance(r, (list, tuple)) and len(r) >= 5:
                rows.append({"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5] if len(r) > 5 else 0})
    out = {}
    for r in rows:
        t = _num(r["t"])
        if t > 1e12:
            t /= 1000.0
        t = int(t)
        out[t] = {"t": t, "o": _num(r["o"]), "h": _num(r["h"]), "l": _num(r["l"]), "c": _num(r["c"]), "v": _num(r["v"])}
    return [out[k] for k in sorted(out)]


def drop_unclosed(bars, tf_menit, server_time_s):
    """Buang setiap bar dengan Time + durasi timeframe > server_time (candle masih berjalan)."""
    tf = tf_menit * 60
    return [b for b in bars if b["t"] + tf <= server_time_s]


def parse_depth(d):
    """{buy:[[harga, jumlah],...], sell:[...]} -> bids turun, asks naik, angka float."""
    if not isinstance(d, dict):
        return None

    def lv(xs):
        out = []
        for x in xs or []:
            if isinstance(x, dict):
                p, q = x.get("price"), x.get("amount", x.get("volume"))
            else:
                p, q = x[0], x[1]
            p, q = _num(p), _num(q)
            if p > 0 and q > 0:
                out.append((p, q))
        return out
    bids = sorted(lv(d.get("buy")), key=lambda x: -x[0])
    asks = sorted(lv(d.get("sell")), key=lambda x: x[0])
    return {"bids": bids, "asks": asks}


def parse_pairs(d):
    """Daftar pasangan -> {pair_id: meta}. Field yang dipakai: trade_fee_percent_taker,
    trade_min_base_currency, volume_precision."""
    out = {}
    if isinstance(d, list):
        for r in d:
            if isinstance(r, dict) and r.get("id"):
                out[str(r["id"]).lower()] = r
    return out
