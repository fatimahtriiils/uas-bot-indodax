"""Storage persisten: Upstash Redis (REST, dari Vercel Marketplace) atau file lokal untuk uji.

Kunci (prefix uas:):
  uas:state              JSON state bot (kas, posisi, candle terakhir, dsb.)
  uas:kill_switch        "1" = kill switch aktif (bisa diubah tanpa deploy ulang)
  uas:lock               kunci agar dua panggilan tidak berjalan bersamaan
  uas:candle:<t>         penanda idempotency: candle <t> sudah diproses
  uas:log:<nama>         list JSON (calls, signals, orders, equity, errors, events, snapshots)
"""
import json
import os
import urllib.request

P = "uas:"
LOGS = ["calls", "signals", "orders", "equity", "errors", "events", "snapshots"]


class UpstashStore:
    def __init__(self, url=None, token=None, timeout=10):
        self.url = (url or os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
        self.token = token or os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        if not self.url or not self.token:
            raise RuntimeError("Env KV_REST_API_URL/KV_REST_API_TOKEN (atau UPSTASH_REDIS_REST_*) belum diisi.")
        self.timeout = timeout

    def _post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        last = None
        for _ in range(3):                          # retry terbatas
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except Exception as e:                  # noqa: BLE001
                last = e
        raise RuntimeError(f"Storage gagal: {last}")

    def cmd(self, *args):
        r = self._post("", [str(a) for a in args])
        if isinstance(r, dict) and r.get("error"):
            raise RuntimeError(r["error"])
        return r.get("result")

    def pipeline(self, cmds, atomic=False):
        if not cmds:
            return []
        r = self._post("/multi-exec" if atomic else "/pipeline", [[str(a) for a in c] for c in cmds])
        for x in r:
            if isinstance(x, dict) and x.get("error"):
                raise RuntimeError(x["error"])
        return [x.get("result") for x in r]

    # API sederhana
    def get(self, k):
        return self.cmd("GET", P + k)

    def set(self, k, v, nx=False, ex=None):
        args = ["SET", P + k, v] + (["NX"] if nx else []) + (["EX", ex] if ex else [])
        return self.cmd(*args) == "OK"

    def delete(self, k):
        self.cmd("DEL", P + k)

    def lrange(self, name):
        out, start, step = [], 0, 1000
        while True:
            chunk = self.cmd("LRANGE", P + "log:" + name, start, start + step - 1) or []
            out.extend(json.loads(x) for x in chunk)
            if len(chunk) < step:
                return out
            start += step

    def commit(self, state, appends):
        """Simpan state + semua baris log baru dalam SATU transaksi atomik."""
        cmds = [["SET", P + "state", json.dumps(state)]]
        for name, rows in appends.items():
            for i in range(0, len(rows), 50):
                cmds.append(["RPUSH", P + "log:" + name] + [json.dumps(r) for r in rows[i:i + 50]])
        self.pipeline(cmds, atomic=True)

    def append(self, name, rows):
        if rows:
            self.pipeline([["RPUSH", P + "log:" + name] + [json.dumps(r) for r in rows]])


class FileStore:
    """Pengganti Redis untuk uji lokal (satu file JSON)."""

    def __init__(self, path):
        self.path = path
        self.d = {"kv": {}, "logs": {n: [] for n in LOGS}}
        if os.path.exists(path):
            with open(path) as f:
                self.d = json.load(f)

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.d, f)

    def get(self, k):
        return self.d["kv"].get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d["kv"]:
            return False
        self.d["kv"][k] = v
        self._save()
        return True

    def delete(self, k):
        self.d["kv"].pop(k, None)
        self._save()

    def lrange(self, name):
        return list(self.d["logs"].get(name, []))

    def commit(self, state, appends):
        self.d["kv"]["state"] = json.dumps(state)
        for n, rows in appends.items():
            self.d["logs"].setdefault(n, []).extend(rows)
        self._save()

    def append(self, name, rows):
        self.d["logs"].setdefault(name, []).extend(rows)
        self._save()


def get_store():
    if os.environ.get("UAS_FILESTORE"):
        return FileStore(os.environ["UAS_FILESTORE"])
    return UpstashStore()


def read_all_logs(store):
    return {n: store.lrange(n) for n in LOGS}
