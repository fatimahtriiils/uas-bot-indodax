"""Uji otomatis. Data di file ini SINTETIS dan HANYA untuk menguji kode (paritas sinyal,
idempotency, eksekusi order book, retry). Tidak pernah dipakai sebagai hasil.

  python -m unittest tests/test_bot.py -v
"""
import copy
import json
import math
import os
import random
import sys
import tempfile
import unittest
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import strategy as S  # noqa: E402
from core.backtest_engine import run_backtest  # noqa: E402
from core.bot import run_tick  # noqa: E402
from core.config import load_config  # noqa: E402
from core.execution import walk_buy_idr, walk_qty  # noqa: E402
from core.indodax import Indodax, parse_candles, drop_unclosed  # noqa: E402
from core.metrics import trades_from_orders, max_drawdown  # noqa: E402
from core.report import build_report  # noqa: E402
from core.store import FileStore  # noqa: E402

TF = 240 * 60
OFF = 3600            # sengaja tidak sejajar epoch, menguji deteksi perataan candle


def test_cfg():
    cfg = load_config(require_fee=False)
    cfg = copy.deepcopy(cfg)
    cfg.update({"fee_taker_beli_persen_resmi": 0.20555, "fee_taker_jual_persen_resmi": 0.41555, "fee_api_taker_persen_dicatat": 0.2,
                "slope_min": 0.0, "atr_rasio_max": 1.3, "hold_bars": 12,
                "forward_mulai": None, "forward_selesai": None})
    return cfg


def synth(pairs, n, seed=7, t0=1_700_000_000 // TF * TF + OFF):
    rnd = random.Random(seed)
    out = {}
    for k, pair in enumerate(pairs):
        px = 1000.0 * (k + 1) ** 2
        bars = []
        drift = 0.0
        for i in range(n):
            if i % 60 == 0:
                drift = rnd.choice([0.004, 0.002, -0.002, 0.0])
            o = px
            c = o * math.exp(drift + rnd.gauss(0, 0.01))
            h = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
            lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
            bars.append({"t": t0 + i * TF, "o": o, "h": h, "l": lo, "c": c, "v": rnd.uniform(1, 9)})
            px = c
        out[pair] = bars
    return out


class FakeAPI:
    """Meniru endpoint publik Indodax di atas candle sintetis; jam dikendalikan uji."""

    def __init__(self, candles, now, half_spread=0.0008, fail_depth=None):
        self.c, self.now, self.hs = candles, now, half_spread
        self.fail_depth = fail_depth or set()

    def opener(self, url):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/server_time":
            return 200, json.dumps({"timezone": "UTC", "server_time": int(self.now * 1000)}).encode()
        if u.path == "/api/pairs":
            return 200, json.dumps([{"id": p, "trade_fee_percent_taker": "0.2", "trade_min_base_currency": "10000",
                                     "volume_precision": 0} for p in self.c]).encode()
        if u.path == "/tradingview/history_v2":
            bars = self.c[q["symbol"].lower()]
            sel = [b for b in bars if int(q["from"]) <= b["t"] <= int(q["to"]) and b["t"] <= self.now]
            return 200, json.dumps([{"Time": b["t"], "Open": b["o"], "High": b["h"], "Low": b["l"],
                                     "Close": b["c"], "Volume": str(b["v"])} for b in sel]).encode()
        if u.path.startswith("/api/depth/"):
            pair = u.path.rsplit("/", 1)[1]
            if pair in self.fail_depth:
                raise TimeoutError("timeout simulasi")
            cur = [b for b in self.c[pair] if b["t"] <= self.now][-1]
            mid = cur["c"]
            bids = [[str(mid * (1 - self.hs - 0.0005 * i)), str(30000000 / mid)] for i in range(20)]
            asks = [[str(mid * (1 + self.hs + 0.0005 * i)), str(30000000 / mid)] for i in range(20)]
            return 200, json.dumps({"buy": bids, "sell": asks}).encode()
        return 404, b""


def client(api):
    return Indodax(timeout=1, retries=3, min_interval=0, sleeper=lambda s: None, opener=api.opener)


class TestEksekusi(unittest.TestCase):
    def test_walk_buy_multi_level(self):
        asks = [(100.0, 1.0), (101.0, 2.0), (105.0, 10.0)]
        f = walk_buy_idr(asks, 302.0, 4)            # 1@100 + 2@101 = 302
        self.assertAlmostEqual(f["qty"], 3.0)
        self.assertAlmostEqual(f["avg"], 302 / 3)
        self.assertEqual(f["levels"], 2)

    def test_walk_sell_kedalaman_kurang(self):
        f = walk_qty([(99.0, 1.0), (98.0, 1.0)], 3.0)
        self.assertEqual(f["parsial"], "kedalaman_kurang")
        self.assertAlmostEqual(f["qty"], 2.0)

    def test_candle_berjalan_dibuang(self):
        bars = parse_candles([{"Time": 0, "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1},
                              {"Time": TF, "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}])
        self.assertEqual(len(drop_unclosed(bars, 240, TF + 10)), 1)   # bar kedua belum tertutup

    def test_retry_terbatas(self):
        n = {"k": 0}

        def op(url):
            n["k"] += 1
            if n["k"] < 3:
                raise TimeoutError("x")
            return 200, b'{"server_time": 1700000000000}'
        c = Indodax(retries=3, min_interval=0, sleeper=lambda s: None, opener=op)
        self.assertEqual(c.server_time(), 1700000000.0)
        self.assertEqual(len(c.errors), 2)
        c2 = Indodax(retries=3, min_interval=0, sleeper=lambda s: None, opener=lambda u: (_ for _ in ()).throw(TimeoutError()))
        self.assertIsNone(c2.server_time())
        self.assertEqual(len(c2.errors), 3)

    def test_mdd(self):
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 130]), -0.25)


class TestBotDanParitas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = test_cfg()
        pairs = list(dict.fromkeys(cls.cfg["pairs"] + [cls.cfg["pair_rezim"]]))
        cls.candles = synth(pairs, 900)
        cls.costs = {"pairs": {p: {"half_spread_median": 0.0008, "slip_buy_median": 0.0001, "slip_sell_median": 0.0001}
                               for p in pairs}}

    def simulate(self, start, hours, store, api, kill_at=None):
        calls = []
        for h in range(hours):
            api.now = start + h * 3600 + 7 * 60
            if kill_at is not None and h == kill_at:
                store.set("kill_switch", "1")
            calls.append(run_tick(store, client(api), self.cfg, sumber="github", clock=lambda: api.now))
        return calls

    def test_paritas_sinyal_100_persen(self):
        tmp = tempfile.mkdtemp()
        store = FileStore(os.path.join(tmp, "s.json"))
        bars = self.candles["btcidr"]
        fa = bars[600]["t"]                         # batas candle
        api = FakeAPI(self.candles, fa)
        calls = self.simulate(fa, 240, store, api)
        self.assertTrue(all(c["status"] in ("ok", "candle_sudah_diproses") for c in calls), {c["status"] for c in calls})
        fb = fa + 240 * 3600
        bt = run_backtest(self.cfg, self.candles, self.costs, fa, fb, simpan_sinyal=True, qty_dec={p: 8 for p in self.candles})
        cfg = dict(self.cfg, forward_mulai=None, forward_selesai=None)
        logs = {n: store.lrange(n) for n in ("calls", "signals", "orders", "equity", "errors", "events", "snapshots")}
        cfg["forward_mulai"] = __import__("core.bot", fromlist=["iso"]).iso(fa)
        cfg["forward_selesai"] = __import__("core.bot", fromlist=["iso"]).iso(fb)
        rep = build_report(cfg, logs, None, bt, self.costs, now_ts=fb)
        sg = rep["gap"]["sinyal"]
        self.assertGreater(sg["dibandingkan"], 50)
        self.assertEqual(sg["identik"], sg["dibandingkan"], [r for r in sg["baris_penting"] if r["cocok"] is False][:3])
        self.assertEqual(sg["hanya_backtest"], 0)
        self.assertEqual(rep["operasional"]["uptime"], 1.0)
        bo = sorted((o["pair"], o["candle_t"], o["sisi"]) for o in bt["orders"])
        fo = sorted((o["pair"], o["candle_t"], o["sisi"]) for o in logs["orders"])
        self.assertEqual(bo, fo)
        self.assertGreater(len(fo), 0, "fixture harus menghasilkan trade agar paritas order teruji")
        tr, _ = trades_from_orders(logs["orders"])
        for t in tr:   # laba bersih = laba kotor - biaya (identitas akuntansi)
            self.assertAlmostEqual(t["laba_kotor_idr"] - t["biaya_idr"], t["laba_bersih_idr"], places=4)
        # equity terakhir = kas + qty x best bid
        last = logs["calls"][-1]
        self.assertAlmostEqual(last["equity"], round(last["kas"] + sum(v["qty"] * v["bid"] for v in last["posisi"].values()), 2), places=1)

    def test_idempotency_dan_lock(self):
        tmp = tempfile.mkdtemp()
        store = FileStore(os.path.join(tmp, "s.json"))
        fa = self.candles["btcidr"][700]["t"]
        api = FakeAPI(self.candles, fa + 7 * 60)
        c1 = run_tick(store, client(api), self.cfg, clock=lambda: api.now)
        n_orders = len(store.lrange("orders"))
        c2 = run_tick(store, client(api), self.cfg, clock=lambda: api.now)
        self.assertEqual(c1["status"], "ok")
        self.assertEqual(c2["status"], "candle_sudah_diproses")
        self.assertEqual(len(store.lrange("orders")), n_orders)
        store.set("lock", "lain")
        c3 = run_tick(store, client(api), self.cfg, clock=lambda: api.now)
        self.assertEqual(c3["status"], "dilewati_panggilan_lain_berjalan")

    def test_kill_switch_dan_error_api(self):
        tmp = tempfile.mkdtemp()
        store = FileStore(os.path.join(tmp, "s.json"))
        fa = self.candles["btcidr"][600]["t"]
        api = FakeAPI(self.candles, fa, fail_depth={"ethidr"})
        self.simulate(fa, 240, store, api, kill_at=0)
        self.assertEqual([o for o in store.lrange("orders") if o["sisi"] == "buy"], [])
        ev = [e["jenis"] for e in store.lrange("events")]
        self.assertIn("kill_switch_ON", ev)
        errs = store.lrange("errors")
        self.assertTrue(any(e["jenis"] == "timeout_atau_jaringan" for e in errs))
        self.assertTrue(any(s["orderbook"] == "orderbook_kosong" for s in store.lrange("signals") if s["pair"] == "ethidr"))

    def test_data_ditolak_403_dicoba_lagi(self):
        """Semua request ditolak (HTTP 403) -> candle TIDAK ditandai selesai; panggilan berikutnya memprosesnya."""
        import urllib.error
        tmp = tempfile.mkdtemp()
        store = FileStore(os.path.join(tmp, "s.json"))
        fa = self.candles["btcidr"][700]["t"]
        api = FakeAPI(self.candles, fa + 7 * 60)

        def tolak(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        c403 = Indodax(timeout=1, retries=3, min_interval=0, sleeper=lambda s: None, opener=tolak)
        r1 = run_tick(store, c403, self.cfg, clock=lambda: api.now)
        self.assertEqual(r1["status"], "data_gagal_dicoba_lagi")
        self.assertIsNone(r1["candle_diproses"])
        self.assertIn("data_gagal_dicoba_lagi", [e["jenis"] for e in store.lrange("events")])
        self.assertTrue(all(e["jenis"] == "http_403" for e in store.lrange("errors")))
        api.now += 30 * 60
        r2 = run_tick(store, client(api), self.cfg, clock=lambda: api.now)
        self.assertEqual(r2["status"], "ok")
        self.assertIsNotNone(r2["candle_diproses"])

    def test_daily_loss_limit_memblokir_posisi_baru(self):
        """Equity di bawah batas harian -> event daily_loss_limit tercatat dan tidak ada pembelian hari itu."""
        import json as _j
        from core.bot import wib_date
        tmp = tempfile.mkdtemp()
        store = FileStore(os.path.join(tmp, "s.json"))
        fa = self.candles["btcidr"][700]["t"]
        api = FakeAPI(self.candles, fa + 7 * 60)
        st = {"kas": 100000000.0, "posisi": {}, "last_candle_t": None, "hari": wib_date(api.now),
              "equity_awal_hari": 200000000.0, "dll_hari": None, "kill_terakhir": False, "seq": 0, "last_bid": {}}
        store.set("state", _j.dumps(st))
        run_tick(store, client(api), self.cfg, clock=lambda: api.now)
        self.assertIn("daily_loss_limit", [e["jenis"] for e in store.lrange("events")])
        self.assertEqual([o for o in store.lrange("orders") if o["sisi"] == "buy"], [])

    def test_jendela_indikator_tidak_tergantung_panjang_data(self):
        b = self.candles["ethidr"]
        i = 800
        L = S.lookback_bars(self.cfg)
        e1 = S.evaluate(b, i, self.cfg)
        e2 = S.evaluate(b[i - L - 5: i + 1], L + 5, self.cfg)
        for k in ("close", "level", "sma", "slope", "atr", "atrp_mean", "entry_raw"):
            self.assertEqual(e1[k], e2[k])


if __name__ == "__main__":
    unittest.main()
