import hmac
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def authorized(handler):
    """Endpoint dilindungi CRON_SECRET (header Authorization: Bearer <secret>)."""
    secret = os.environ.get("CRON_SECRET", "")
    if len(secret) < 16:
        return False                    # tolak bila secret belum diset / terlalu pendek
    got = handler.headers.get("Authorization", "")
    return hmac.compare_digest(got.encode(), f"Bearer {secret}".encode())


def send_json(handler, code, obj, cache=None):
    body = json.dumps(obj, ensure_ascii=False, default=str).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", cache or "no-store")
    handler.end_headers()
    handler.wfile.write(body)
