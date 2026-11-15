"""Download real SPHEREx cutouts from the observation list the notebook saved. Standard library only, so it runs anywhere.
It is built for a flaky service: retries with backoff, skips files you already have, pauses between requests,
checks every file is really FITS. Just re-run it until everything is present.

    python scripts/download_cutouts.py --csv data/reports/irsa_observations.csv --ra 269.452 --dec 4.693 --n 12
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = {"User-Agent": "SkyDetective/0.1 (NASA Space Apps 2026 project)"}


def cutout_url(uri: str, ra: float, dec: float, size: float) -> str:
    if "center=" in uri:  # the notebook's CSV holds the bare file URL; tolerate full cutout URLs too
        return uri
    return f"{uri}?center={ra},{dec}d&size={size}"


def fetch(url: str, dest: Path, tries: int) -> bool:
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                data = r.read()
            if not data.startswith(b"SIMPLE"):
                raise ValueError("not FITS: " + data[:80].decode(errors="replace"))
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return True
        except Exception as e:
            body = e.read()[:120].decode(errors="replace") if isinstance(e, urllib.error.HTTPError) else ""
            wait = 5 * 2**k
            print(f"    attempt {k + 1}/{tries}: {repr(e)[:100]} {body.replace(chr(10), ' ')[:80]}" + (f" (wait {wait}s)" if k + 1 < tries else ""))
            if k + 1 < tries:
                time.sleep(wait)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/reports/irsa_observations.csv")
    ap.add_argument("--ra", type=float, required=True)
    ap.add_argument("--dec", type=float, required=True)
    ap.add_argument("--size", type=float, default=0.1, help="cutout width in degrees")
    ap.add_argument("--n", type=int, default=12, help="how many rows (oldest first)")
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--tries", type=int, default=5)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between files (be polite to IRSA)")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv, newline="")))[: a.n]
    if not rows or "uri" not in rows[0]:
        sys.exit(f"{a.csv} has no 'uri' column. Run the notebook query cell first.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, row in enumerate(rows):
        name = Path(row["uri"].split("?")[0]).name
        dest = out / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"[{i + 1}/{len(rows)}] have {name}")
            ok += 1
            continue
        print(f"[{i + 1}/{len(rows)}] {name}")
        if fetch(cutout_url(row["uri"], a.ra, a.dec, a.size), dest, a.tries):
            ok += 1
            time.sleep(a.pause)
    print(f"\n{ok}/{len(rows)} cutouts in {out}. " + ("Re-run to retry the missing ones." if ok < len(rows) else "All done."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
