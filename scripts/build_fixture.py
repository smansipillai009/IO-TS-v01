"""Turn real FITS cutouts you already have (from the notebook, curl, or download_cutouts.py) into an OFFLINE archive
the backend can serve with no IRSA access at all:

    python scripts/build_fixture.py --fits data/raw --out data/fixtures
    FIXTURE_DIR=data/fixtures uvicorn app.main:app --port 8000        (PowerShell: $env:FIXTURE_DIR="data/fixtures"; uvicorn ...)

Then search the SAME position the cutouts were made for (the cutout centre), with radius <= half the cutout width
(0.1 deg cutouts -> radius 0.05). These are real SPHEREx files, so results are real; only the archive lookup is skipped."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from astropy.io import fits

_FNAME_DET = re.compile(r"[_-]\d?D([1-6])[_.]")


def read_meta(path: Path) -> dict | None:
    with fits.open(path, memmap=False) as h:
        hdrs = [h[0].header] + [x.header for x in h if (x.name or "").upper() == "IMAGE"]
        get = lambda *keys: next((hd[k] for hd in hdrs for k in keys if k in hd and hd[k] not in (None, "")), None)
        mjd = get("MJD-AVG", "MJD-OBS", "MJD_AVG")
        det = get("DETECTOR")
    try:
        det = int(str(det).strip().lstrip("Dd"))
    except (TypeError, ValueError):
        m = _FNAME_DET.search(path.name)
        det = int(m.group(1)) if m else None
    if mjd is None or det not in range(1, 7):
        print(f"  skip {path.name}: mjd={mjd} detector={det} (not readable as a SPHEREx cutout)")
        return None
    return {"obs_id": path.stem, "access_url": "https://irsa.ipac.caltech.edu/fixture", "t_mjd": float(mjd),
            "em_min_um": None, "em_max_um": None, "detector": det, "release": "local-real"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", default="data/raw")
    ap.add_argument("--out", default="data/fixtures")
    a = ap.parse_args()
    files = sorted(Path(a.fits).glob("*.fits"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    obs = []
    for f in files:
        m = read_meta(f)
        if m:
            shutil.copyfile(f, out / f"{m['obs_id']}.fits")
            obs.append(m)
    if not obs:
        sys.exit(f"No usable .fits files in {a.fits}")
    obs.sort(key=lambda o: o["t_mjd"])
    (out / "observations.json").write_text(json.dumps(obs, indent=2))
    dets = sorted({o["detector"] for o in obs})
    print(f"Wrote {len(obs)} observations (detectors {dets}) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
