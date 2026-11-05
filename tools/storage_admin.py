"""Administrasi storage dari LAPTOP (butuh KV_REST_API_URL/TOKEN di .env atau environment).

  python tools/storage_admin.py export                 # unduh semua log mentah -> logs/*.jsonl
  python tools/storage_admin.py status                 # ringkas jumlah baris log, state, kill switch
  python tools/storage_admin.py kill on "alasan"       # kill switch ON (dicatat di log events)
  python tools/storage_admin.py kill off "alasan"
  python tools/storage_admin.py catat-bugfix "deskripsi perbaikan + hash commit"   # wajib untuk bug fix saat forward test
  python tools/storage_admin.py reset --ya             # HANYA saat freeze: ekspor dulu ke logs/pra_freeze/, lalu kosongkan state & log
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import ROOT  # noqa: E402
from core.results_io import write_jsonl  # noqa: E402
from core.store import LOGS, P, UpstashStore  # noqa: E402


def load_env():
    fn = os.path.join(ROOT, ".env")
    if os.path.exists(fn):
        for line in open(fn):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export(st, folder):
    os.makedirs(folder, exist_ok=True)
    for n in LOGS:
        rows = st.lrange(n)
        write_jsonl(os.path.join(folder, n + ".jsonl"), rows)
        print(f"{n:<10} {len(rows):>6} baris -> {os.path.relpath(os.path.join(folder, n + '.jsonl'), ROOT)}")
    s = st.get("state")
    with open(os.path.join(folder, "state.json"), "w") as f:
        f.write(json.dumps(json.loads(s), indent=1) if s else "null")


def main():
    load_env()
    st = UpstashStore()
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "export":
        export(st, os.path.join(ROOT, "logs"))
    elif a[0] == "status":
        for n in LOGS:
            print(n, st.cmd("LLEN", P + "log:" + n))
        print("kill_switch:", st.get("kill_switch"))
        print("state:", (st.get("state") or "")[:600])
    elif a[0] == "kill" and len(a) >= 2 and a[1] in ("on", "off"):
        v = "1" if a[1] == "on" else "0"
        st.set("kill_switch", v)
        st.append("events", [{"waktu_utc": now(), "jenis": "kill_switch_diubah", "nilai": v,
                              "keterangan": " ".join(a[2:]), "sumber": "tools/storage_admin.py"}])
        print("kill_switch =", v)
    elif a[0] == "catat-bugfix" and len(a) >= 2:
        st.append("events", [{"waktu_utc": now(), "jenis": "perbaikan_bug", "keterangan": " ".join(a[1:]),
                              "sumber": "tools/storage_admin.py"}])
        print("tercatat.")
    elif a[0] == "reset" and "--ya" in a:
        export(st, os.path.join(ROOT, "logs", "pra_freeze"))
        cmds = [["DEL", P + "state"], ["DEL", P + "lock"]] + [["DEL", P + "log:" + n] for n in LOGS if n != "snapshots"]
        keys = st.cmd("KEYS", P + "candle:*") or []
        cmds += [["DEL", k] for k in keys]
        st.pipeline(cmds)
        st.append("events", [{"waktu_utc": now(), "jenis": "reset_freeze",
                              "keterangan": "state & log dikosongkan saat freeze; log pra-freeze diarsip di logs/pra_freeze/. Snapshot dipertahankan."}])
        print("Reset selesai. Snapshot order book dipertahankan.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
