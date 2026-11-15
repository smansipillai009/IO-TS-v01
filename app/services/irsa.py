"""Archive access. Everything IRSA-specific lives in this file so a release or API change is a one-file fix.

Workflow copied from IRSA's official tutorial (https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/):
  * TAP query on spherex.artifact JOIN spherex.plane -> uri + time_bounds_lower + energy_bandpassname ('SPHEREx-D1'..'D6')
  * cutout = <uri>?center=<ra>,<dec>d&size=<deg>   (size is the full cutout width in degrees)
STILL TO CONFIRM in the notebook: the unit of time_bounds_lower (assumed MJD) and that every row is a .fits image."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from ..config import Settings
from ..errors import ArchiveUnavailable, NoCoverage, ValidationFailed
from ..http import request_with_retry
from ..science.grouping import spread
from ..validation import SearchParams

log = logging.getLogger("skydetective.irsa")
MJD_UNIX_EPOCH = 40587.0


@dataclass
class Observation:
    obs_id: str
    access_url: str
    t_mjd: float | None
    em_min_um: float | None
    em_max_um: float | None
    detector: int | None
    release: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Observation":
        return Observation(**d)


class ArchiveClient(Protocol):
    def search(self, p: SearchParams) -> list[Observation]: ...
    def fetch_cutout(self, obs: Observation, p: SearchParams, dest: Path) -> None: ...


def mjd_to_date(mjd: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp((mjd - MJD_UNIX_EPOCH) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")


def est_cutout_mb(p: SearchParams, s: Settings) -> float:
    """Rough size of one cutout: ~5 float32 planes (image, flags, variance, zodi, psf) of the field."""
    n = max(2 * p.radius * 3600 / s.pixel_scale_arcsec, 1)
    return n * n * 4 * 5 / 1e6


def pool_size(p: SearchParams, s: Settings) -> int:
    """How many cutouts to download so we can FIND comparable frames. Never fewer than the display cap, never over budget."""
    want = p.max_frames or s.max_frames
    per = max(est_cutout_mb(p, s), 1e-6)
    return max(want, min(s.max_pool, int(s.max_est_mb // per)))


def select_observations(obs: list[Observation], p: SearchParams, s: Settings) -> list[Observation]:
    """Date filter, detector choice, de-duplication, a time-spread candidate pool, and the download budget."""
    if p.start or p.end:
        obs = [o for o in obs if o.t_mjd is None or ((not p.start or mjd_to_date(o.t_mjd) >= p.start) and (not p.end or mjd_to_date(o.t_mjd) <= p.end))]
    seen, uniq = set(), []
    for o in obs:
        if o.obs_id not in seen:
            seen.add(o.obs_id)
            uniq.append(o)
    obs = uniq
    if p.detector is not None:
        obs = [o for o in obs if o.detector in (None, p.detector)]
    else:
        # frontend contract: "undefined = backend picks the best-covered detector"
        known = Counter(o.detector for o in obs if o.detector)
        if known:
            best = sorted(known.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            obs = [o for o in obs if o.detector in (None, best)]
    obs = sorted(obs, key=lambda o: (o.t_mjd is None, o.t_mjd or 0))
    # Wavelength at the target is only known after download, and it differs exposure to exposure, so keep a pool spread
    # across the whole time range (long baseline) and let the pipeline pick comparable frames from it.
    obs = spread(obs, pool_size(p, s))
    if not obs:
        raise NoCoverage()
    total = len(obs) * est_cutout_mb(p, s)
    if total > s.max_est_mb:
        raise ValidationFailed(
            f"This request would transfer about {total:.0f} MB (limit {s.max_est_mb:.0f} MB). "
            "Choose a smaller field of view or a narrower date range."
        )
    return obs


def build_query(ra: float, dec: float, detector: int | None) -> str:
    """ADQL from the IRSA tutorial. Only floats and a fixed bandpass pattern are interpolated, so no user text reaches the query."""
    band = f"AND p.energy_bandpassname = 'SPHEREx-D{int(detector)}'" if detector else ""
    return (
        "SELECT 'https://irsa.ipac.caltech.edu/' || a.uri AS uri, p.time_bounds_lower, p.energy_bandpassname "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
        f"WHERE 1 = CONTAINS(POINT('ICRS', {float(ra)!r}, {float(dec)!r}), p.poly) {band} "
        "ORDER BY p.time_bounds_lower"
    )


class IrsaArchive:
    def __init__(self, s: Settings):
        self.s = s

    def search(self, p: SearchParams) -> list[Observation]:
        """All SPHEREx spectral images whose footprint contains (ra, dec). One TAP query, optionally one detector."""
        query = build_query(p.ra, p.dec, p.detector)
        from astroquery.ipac.irsa import Irsa

        tbl, last = None, None
        for k in range(self.s.http_retries):  # IRSA answers 502/503 when busy: retry, alternating sync/async
            try:
                res = Irsa.query_tap(query, async_job=(k % 2 == 1))
                tbl = res.to_table() if hasattr(res, "to_table") else res
                break
            except Exception as e:
                last = e
                wait = 3 * 2**k
                log.warning("IRSA TAP attempt %d/%d failed (%s); retry in %ds", k + 1, self.s.http_retries, repr(e)[:120], wait)
                time.sleep(wait)
        if tbl is None:
            raise ArchiveUnavailable() from last
        out: list[Observation] = []
        for row in tbl:
            url = str(row["uri"])
            if not url.lower().split("?")[0].endswith(".fits"):
                continue
            tm = row["time_bounds_lower"]
            band = str(row["energy_bandpassname"]) if "energy_bandpassname" in tbl.colnames else ""
            out.append(
                Observation(
                    obs_id=Path(urlparse(url).path).stem,
                    access_url=url,
                    t_mjd=float(tm) if tm is not None and str(tm) not in ("", "--") else None,
                    em_min_um=None,  # wavelength is read per pixel from WCS-WAVE after download, never from here
                    em_max_um=None,
                    detector=_detector_from(band, url),
                    release=self.s.data_release,
                )
            )
        return out

    def fetch_cutout(self, obs: Observation, p: SearchParams, dest: Path) -> None:
        q = self.s.cutout_query.format(ra=f"{p.ra:.6f}", dec=f"{p.dec:.6f}", size_deg=f"{2 * p.radius:.5f}")
        url = obs.access_url + ("&" if "?" in obs.access_url else "?") + q
        # Only fetch from IPAC/Caltech hosts, never an arbitrary URL (SSRF guard). Fixtures bypass this class.
        host = urlparse(obs.access_url).hostname or ""
        if not host.endswith(".caltech.edu"):
            raise ArchiveUnavailable("Refusing to download from an unexpected host.")
        r = request_with_retry("GET", url, self.s)
        if r.status_code != 200 or not r.content.startswith(b"SIMPLE"):
            raise ArchiveUnavailable(f"The archive did not return a FITS cutout for {obs.obs_id}.")
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest)


def _detector_from(*texts: str) -> int | None:
    for t in texts:
        m = re.search(r"(?:^|[^A-Za-z0-9])D([1-6])(?:[^0-9]|$)", t)
        if m:
            return int(m.group(1))
    return None


def cutout_path(obs: Observation, p: SearchParams, s: Settings) -> Path:
    key = f"{obs.release}|{obs.obs_id}|{p.ra:.5f}|{p.dec:.5f}|{p.radius:.4f}"
    return s.cutout_dir / (hashlib.sha1(key.encode()).hexdigest()[:24] + ".fits")


class FixtureArchive:
    """Offline archive for tests and demo fallback: reads <dir>/observations.json and <dir>/<obs_id>.fits.
    Record real products with scripts/precache_demo.py, then point FIXTURE_DIR at them if IRSA is down on stage."""

    def __init__(self, directory: Path, s: Settings):
        self.dir, self.s = Path(directory), s

    def search(self, p: SearchParams) -> list[Observation]:
        import json

        return [Observation.from_dict(d) for d in json.loads((self.dir / "observations.json").read_text())]

    def fetch_cutout(self, obs: Observation, p: SearchParams, dest: Path) -> None:
        shutil.copyfile(self.dir / f"{obs.obs_id}.fits", dest)
