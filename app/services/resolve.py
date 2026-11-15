"""Object name -> RA/Dec. Local table for the demo targets (works offline), then CDS Sesame."""
from __future__ import annotations

import re

from ..cache import JsonCache
from ..config import Settings
from ..errors import NotFound
from ..http import request_with_retry

# Keep in sync with frontend/src/lib/demoTargets.json
DEMO_TARGETS = {
    "barnard's star": ("Barnard's Star", 269.452, 4.693),
    "barnards star": ("Barnard's Star", 269.452, 4.693),
    "wise 0855": ("WISE 0855", 133.795, -7.245),
    "wise 0855-0714": ("WISE 0855", 133.795, -7.245),
    "epsilon indi b": ("Epsilon Indi B", 330.84, -56.786),
    "rw aurigae": ("RW Aurigae", 76.957, 30.401),
    "rw aur": ("RW Aurigae", 76.957, 30.401),
    "r draconis": ("R Draconis", 248.173, 66.755),
    "r dra": ("R Draconis", 248.173, 66.755),
}
_J = re.compile(r"^%J\s+([0-9.]+)\s+([+-]?[0-9.]+)", re.M)
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV"


def resolve_name(name: str, s: Settings, cache: JsonCache) -> dict:
    key = " ".join(name.lower().split())
    if not key or len(key) > 100:
        raise NotFound("Please enter an object name.")
    if key in DEMO_TARGETS:
        n, ra, dec = DEMO_TARGETS[key]
        return {"name": n, "ra": ra, "dec": dec}
    ck = f"resolve:{key}"
    hit = cache.get(ck, 30 * 86400)
    if hit:
        return hit
    r = request_with_retry("GET", f"{SESAME}?{_quote(name)}", s)
    m = _J.search(r.text)
    if not m:
        raise NotFound(f"Could not find an object called '{name}'. Try another name or enter RA and Dec.")
    out = {"name": name, "ra": float(m.group(1)), "dec": float(m.group(2))}
    cache.set(ck, out)
    return out


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")
