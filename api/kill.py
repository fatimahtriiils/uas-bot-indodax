"""/api/kill?on=1|0&alasan=... — kill switch manual tanpa deploy ulang. Wajib CRON_SECRET. Setiap pemakaian dicatat."""
import datetime as dt
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from api._common import authorized, send_json  # noqa: E402
from core.store import get_store


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not authorized(self):
            send_json(self, 401, {"error": "unauthorized"})
            return
        q = parse_qs(urlparse(self.path).query)
        on = (q.get("on") or [""])[0]
        if on not in ("0", "1"):
            send_json(self, 400, {"error": "pakai on=1 atau on=0"})
            return
        alasan = (q.get("alasan") or [""])[0][:200]
        st = get_store()
        st.set("kill_switch", on)
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        st.append("events", [{"waktu_utc": now, "jenis": "kill_switch_diubah", "nilai": on, "keterangan": alasan,
                              "sumber": "endpoint /api/kill"}])
        send_json(self, 200, {"kill_switch": on, "waktu_utc": now})
