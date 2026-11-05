"""/api/tick — dipanggil penjadwal (GitHub Actions tiap jam menit ke-7). Wajib header CRON_SECRET."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from api._common import authorized, send_json  # noqa: E402
from core.bot import run_tick
from core.config import load_config
from core.indodax import Indodax
from core.store import get_store


class handler(BaseHTTPRequestHandler):
    def _run(self):
        if not authorized(self):
            send_json(self, 401, {"error": "unauthorized"})
            return
        q = parse_qs(urlparse(self.path).query)
        sumber = (q.get("sumber") or ["cron"])[0][:20]
        cfg = load_config()
        client = Indodax(timeout=8, retries=3, min_interval=0.35, budget_s=200)
        call = run_tick(get_store(), client, cfg, sumber=sumber)
        send_json(self, 200, {k: call.get(k) for k in ("id", "status", "candle_diproses_utc", "equity", "n_posisi",
                                                        "n_error_api", "durasi_s", "kill_switch")})

    def do_GET(self):
        self._run()

    def do_POST(self):
        self._run()
