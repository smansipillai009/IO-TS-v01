"""Input validation and the deterministic request id. Pure Python: easy to unit test."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date

from .config import Settings
from .errors import ValidationFailed

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SearchParams:
    ra: float
    dec: float
    radius: float
    start: str | None = None
    end: str | None = None
    detector: int | None = None
    max_frames: int | None = None  # display cap; part of the request id because it changes the result

    def normalized(self) -> dict:
        return {
            "ra": round(self.ra, 5),
            "dec": round(self.dec, 5),
            "radius": round(self.radius, 4),
            "start": self.start,
            "end": self.end,
            "detector": self.detector,
            "max_frames": self.max_frames,
        }


def _check_date(label: str, v: str | None) -> str | None:
    if v in (None, ""):
        return None
    if not _DATE.match(v):
        raise ValidationFailed(f"{label} must be a date like 2025-07-01.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValidationFailed(f"{label} is not a real calendar date.")
    return v


def validate_search(
    ra: float, dec: float, radius: float, start: str | None, end: str | None, detector: int | None, s: Settings,
    max_frames: int | None = None,
) -> SearchParams:
    if not all(math.isfinite(x) for x in (ra, dec, radius)):
        raise ValidationFailed("RA, Dec and radius must be finite numbers.")
    if not (0 <= ra < 360):
        raise ValidationFailed("RA must be between 0 and 360 degrees.")
    if not (-90 <= dec <= 90):
        raise ValidationFailed("Dec must be between -90 and +90 degrees.")
    if radius <= 0:
        raise ValidationFailed("Radius must be above 0 degrees.")
    if radius > s.max_radius_deg:
        raise ValidationFailed(
            f"The requested field of view is too large. The maximum radius is {s.max_radius_deg} degrees."
        )
    start, end = _check_date("Start", start), _check_date("End", end)
    if start and end and start > end:
        raise ValidationFailed("Start date must not be after the end date.")
    if detector is not None and detector not in range(1, 7):
        raise ValidationFailed("Detector must be a number from 1 to 6.")
    if max_frames is not None and not (1 <= max_frames <= s.max_frames):
        raise ValidationFailed(f"Maximum frames must be between 1 and {s.max_frames}.")
    return SearchParams(ra, dec, radius, start, end, detector, max_frames)


def request_id(p: SearchParams, release: str, pipeline_version: str) -> str:
    """Same inputs + same release + same pipeline version -> same id. This is what makes
    identical URLs hit the cache, and what invalidates derived products when the pipeline changes."""
    blob = json.dumps({"p": p.normalized(), "release": release, "pipeline": pipeline_version}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
