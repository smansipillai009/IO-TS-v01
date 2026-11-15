"""Warm the caches for every demo target through the RUNNING API, so the live demo never waits on IRSA.
    uvicorn app.main:app --port 8000   (another terminal)
    python scripts/precache_demo.py http://localhost:8000
Then run the frontend's  node scripts/save-demo.mjs http://localhost:8000  to snapshot manifests + PNGs."""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TARGETS = [("Barnard's Star", 269.452, 4.693), ("WISE 0855", 133.795, -7.245), ("Epsilon Indi B", 330.84, -56.786),
           ("RW Aurigae", 76.957, 30.401), ("R Draconis", 248.173, 66.755)]

for name, ra, dec in TARGETS:
    try:
        r = httpx.get(f"{BASE}/api/search", params={"ra": ra, "dec": dec, "radius": 0.05}, timeout=120)
        if r.status_code != 200:
            print(f"{name}: search failed: {r.json().get('detail')}")
            continue
        rid = r.json()["request_id"]
        for _ in range(180):
            m = httpx.get(f"{BASE}/api/manifest/{rid}", timeout=60).json()
            if m["status"] in ("complete", "failed"):
                break
            time.sleep(1)
        if m["status"] == "complete":
            print(f"{name}: OK  {len(m['frames'])} frames, {len(m['groups'])} groups, {len(m['difference_frames'])} diffs, "
                  f"skipped {len(m['processing']['skipped'])}")
        else:
            print(f"{name}: {m['status']} {m.get('error')}")
    except Exception as e:
        print(f"{name}: ERROR {e}")
