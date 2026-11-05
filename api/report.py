"""/api/report — publik (hanya baca). Menghitung semua angka dashboard dari log di storage."""
from http.server import BaseHTTPRequestHandler

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from api._common import send_json  # noqa: E402
import os
from core.config import load_config, load_costs
from core.report import build_report
from core.results_io import load_results
from core.store import get_store, read_all_logs


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            cfg = load_config(require_fee=False)
            logs = read_all_logs(get_store())
            rep = build_report(cfg, logs, load_results("results/backtest"), load_results("results/window"), load_costs(),
                               info={"sumber": "vercel /api/report (live dari storage)",
                                     "kode": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:12]})
            send_json(self, 200, rep, cache="public, s-maxage=60")
        except Exception as e:  # noqa: BLE001
            send_json(self, 500, {"error": f"{type(e).__name__}: {e}"})
