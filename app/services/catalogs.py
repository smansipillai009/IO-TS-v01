"""Gaia / WISE / SIMBAD cone searches through public TAP services (CSV output, supported everywhere).
Returns {sources:[{id,ra,dec,label}]} exactly as the frontend expects."""
from __future__ import annotations

import csv
import io

from ..cache import JsonCache
from ..config import Settings
from ..errors import ValidationFailed
from ..http import request_with_retry

TAP = {
    "gaia": "https://gea.esac.esa.int/tap-server/tap/sync",
    "wise": None,  # filled from settings.irsa_tap_url
    "simbad": "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
}
TOP = 500


def _cone(ra: float, dec: float, r: float, ra_col="ra", dec_col="dec") -> str:
    return f"1=CONTAINS(POINT('ICRS',{ra_col},{dec_col}),CIRCLE('ICRS',{ra:.6f},{dec:.6f},{r:.6f}))"


def _adql(kind: str, ra: float, dec: float, r: float) -> str:
    if kind == "gaia":
        return (
            f"SELECT TOP {TOP} source_id, ra, dec, phot_g_mean_mag, pmra, pmdec FROM gaiadr3.gaia_source "
            f"WHERE {_cone(ra, dec, r)} ORDER BY phot_g_mean_mag"
        )
    if kind == "wise":
        return f"SELECT TOP {TOP} designation, ra, dec, w1mpro FROM allwise_p3as_psd WHERE {_cone(ra, dec, r)}"
    return f"SELECT TOP {TOP} main_id, ra, dec, otype FROM basic WHERE {_cone(ra, dec, r)}"


def _label(kind: str, row: dict) -> str:
    def f(k, nd=2):
        try:
            return f"{float(row[k]):.{nd}f}"
        except (KeyError, ValueError, TypeError):
            return "?"

    if kind == "gaia":
        # Gaia positions are at epoch 2016.0; proper motion helps explain offsets from SPHEREx positions.
        return f"Gaia DR3 {row.get('source_id')} G={f('phot_g_mean_mag')} pm=({f('pmra', 1)}, {f('pmdec', 1)}) mas/yr"
    if kind == "wise":
        return f"AllWISE {row.get('designation')} W1={f('w1mpro')}"
    return f"SIMBAD {row.get('main_id', '').strip()} ({row.get('otype', '?')})"


def cone_search(kind: str, ra: float, dec: float, radius: float, s: Settings, cache: JsonCache) -> dict:
    if kind not in TAP:
        raise ValidationFailed("Catalog must be gaia, wise or simbad.")
    if not (0 < radius <= s.max_radius_deg * 1.5 + 1e-9):
        raise ValidationFailed("Catalog radius is out of range.")
    key = f"cat:{kind}:{ra:.4f}:{dec:.4f}:{radius:.4f}"
    hit = cache.get(key, 7 * 86400)
    if hit:
        return hit
    url = TAP[kind] or s.irsa_tap_url.rstrip("/") + "/sync"
    r = request_with_retry(
        "POST", url, s, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": _adql(kind, ra, dec, radius)}
    )
    sources = []
    for i, row in enumerate(csv.DictReader(io.StringIO(r.text))):
        try:
            sources.append(
                {
                    "id": str(row.get("source_id") or row.get("designation") or row.get("main_id") or f"{kind}-{i}").strip(),
                    "ra": float(row["ra"]),
                    "dec": float(row["dec"]),
                    "label": _label(kind, row),
                }
            )
        except (KeyError, ValueError):
            continue  # skip rows without usable coordinates
    out = {"sources": sources, "truncated": len(sources) >= TOP}
    cache.set(key, out)
    return out
