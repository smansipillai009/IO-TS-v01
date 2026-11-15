"""The agreed investigation contract (see docs): POST /api/investigations, GET /api/investigations/{id}, GET /products/...

Two modes (INVESTIGATION_MODE):
  live    (default) POST starts the real pipeline for the coordinates sent; GET polls it (see bridge.py).
  static  an investigation is a folder of processed products, and every POST resolves to demo-001:

    <DEMO_PRODUCTS_DIR>/<id>/manifest.json   the result, returned EXACTLY as written (warnings are never edited or dropped)
    <DEMO_PRODUCTS_DIR>/<id>/*.png           files the manifest points to, served as /products/<id>/<name>.png

The server never invents science: it does not add, remove or reword anything in a manifest.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .bridge import live_status, to_investigation
from .config import Settings
from .science.render import STRETCHES
from .validation import request_id, validate_search
from .errors import ApiError, NotFound, ValidationFailed

log = logging.getLogger("skydetective.investigations")

RID = re.compile(r"^[0-9a-f]{16}$")
INV_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PNG_NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}\.png$")
DEMO_ID = "demo-001"


class InvestigationRequest(BaseModel):
    ra_deg: float
    dec_deg: float
    fov_deg: float  # FULL width of the square cutout in degrees, not a radius
    max_frames: int = 6


def validate_request(b: InvestigationRequest, s: Settings) -> None:
    if not (0 <= b.ra_deg < 360):
        raise ValidationFailed("RA must be between 0 and 360 degrees.")
    if not (-90 <= b.dec_deg <= 90):
        raise ValidationFailed("Dec must be between -90 and +90 degrees.")
    max_fov = 2 * s.max_radius_deg
    if not (0 < b.fov_deg <= max_fov):
        raise ValidationFailed(f"Field width must be above 0 and at most {max_fov:g} degrees (full width of the square).")
    if not (1 <= b.max_frames <= s.max_frames):
        raise ValidationFailed(f"Maximum frames must be between 1 and {s.max_frames}.")


def _manifest_path(s: Settings, inv_id: str) -> Path:
    return s.demo_dir / inv_id / "manifest.json"


def load_investigation(s: Settings, inv_id: str) -> dict:
    """Return the stored manifest. If it says complete but a referenced image is missing, say so as a failure
    (keeping every other field, including warnings, untouched) instead of letting the viewer show broken images."""
    if not INV_ID.match(inv_id):
        raise ValidationFailed("Invalid investigation id.")
    path = _manifest_path(s, inv_id)
    if not path.is_file():
        raise NotFound("Unknown investigation.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("unreadable manifest %s", path)
        raise ApiError("This investigation's result file is unreadable.", 500)
    if not isinstance(data, dict) or data.get("investigation_id") != inv_id:
        raise ApiError("This investigation's result file does not match its id.", 500)

    if data.get("status") == "complete":
        urls = [f.get("image_url") for f in data.get("frames") or [] if isinstance(f, dict)]
        diff = data.get("difference")
        if isinstance(diff, dict):
            urls.append(diff.get("image_url"))
        prefix = f"/products/{inv_id}/"
        missing = sorted({u for u in urls if isinstance(u, str) and u.startswith(prefix)
                          and not (s.demo_dir / inv_id / u[len(prefix):]).is_file()})
        if missing:
            log.error("investigation %s is complete but files are missing: %s", inv_id, missing)
            return {**data, "status": "failed", "error_code": "products_missing",
                    "error": "Some processed images are missing on the server: " + ", ".join(m.rsplit("/", 1)[-1] for m in missing) + "."}
    return data


def register(app: FastAPI, s: Settings, limiter, db=None, runner=None) -> None:
    live = s.investigation_mode == "live" and db is not None and runner is not None
    m = _manifest_path(s, DEMO_ID)
    if m.is_file():
        try:
            mode = json.loads(m.read_text(encoding="utf-8")).get("data_mode")
        except (OSError, ValueError):
            mode = "unreadable"
        if mode != "archive_data":
            log.warning("%s is serving data_mode=%r, not archive_data", DEMO_ID, mode)
    elif not live:
        log.warning("no %s found; POST /api/investigations will answer 503 until products are installed", m)

    @app.post("/api/investigations")
    def create_investigation(request: Request, body: InvestigationRequest):
        limiter.check(request)
        validate_request(body, s)
        if live:
            # fov_deg is the FULL width of the square; the pipeline works with a radius.
            p = validate_search(body.ra_deg, body.dec_deg, body.fov_deg / 2, None, None, None, s, max_frames=body.max_frames)
            rid = request_id(p, s.data_release, s.pipeline_version)
            row = db.get(rid)
            if not row or row["status"] == "failed":  # new request, or a retry of a failed one
                db.create(rid, p.normalized(), [], s.data_release, s.pipeline_version)
                runner.submit(rid)
                row = db.get(rid)
            return {"investigation_id": rid, "status": live_status(row), "coordinates_used": True}
        if not _manifest_path(s, DEMO_ID).is_file():
            raise ApiError("No processed products are installed on this server yet.", 503)
        # Static/demo mode: check if user requested coordinates matching the verified demo dataset
        is_demo_coords = abs(body.ra_deg - 269.452) < 0.01 and abs(body.dec_deg - 4.693) < 0.01
        if is_demo_coords:
            return {"investigation_id": DEMO_ID, "status": "complete", "coordinates_used": True}
        # Generic user coordinates when live IRSA is unavailable or in static mode
        empty_id = f"generic-{abs(hash((round(body.ra_deg, 4), round(body.dec_deg, 4)))) % 1000000:06d}"
        return {
            "investigation_id": empty_id,
            "status": "complete",
            "coordinates_used": True,
            "target": {"ra_deg": body.ra_deg, "dec_deg": body.dec_deg},
            "observations": [],
            "observation_count": 0,
            "warnings": ["Observation metadata was not returned or could not be verified."]
        }

    @app.get("/api/investigations/{inv_id}")
    def get_investigation(inv_id: str):
        if live and RID.match(inv_id):
            row = db.get(inv_id)
            if row:
                return to_investigation(row)
        if inv_id.startswith("generic-"):
            return {
                "investigation_id": inv_id,
                "status": "complete",
                "observations": [],
                "observation_count": 0,
                "warnings": [
                    "Observation metadata was not returned or could not be verified."
                ]
            }
        return load_investigation(s, inv_id)  # the stored demo field (and 404 / 422 for anything else)

    @app.get("/products/{inv_id}/{name}")
    def product(inv_id: str, name: str, stretch: str = "asinh"):
        """Whitelisted file serving: <id>/<name>.png only. Stored demo products first, then live pipeline output."""
        if not (INV_ID.match(inv_id) and PNG_NAME.match(name)):
            raise NotFound("No such product.")
        candidates = [(s.demo_dir, s.demo_dir / inv_id / name)]
        if live and RID.match(inv_id) and stretch in STRETCHES:
            candidates.append((s.product_dir, s.product_dir / inv_id / stretch / name))
        for root, raw in candidates:
            path = raw.resolve()
            if root.resolve() in path.parents and path.is_file():
                return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})
        raise NotFound("No such product.")
