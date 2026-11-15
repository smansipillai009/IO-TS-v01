"""Sky Detective API. Run:  uvicorn app.main:app --reload --port 8000   (docs at /docs)"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .cache import JsonCache
from .config import Settings, get_settings
from .db import Database
from . import investigations
from .errors import ApiError, ArchiveUnavailable, NotFound, ValidationFailed
from .science.combine import combine
from .science.fits_io import open_cutout
from .science.render import STRETCHES
from .science.spectrum import FLUX_UNIT, extract_point
from .services.catalogs import cone_search
from .services.archive_search import archive_search as _archive_search
from .services.irsa import ArchiveClient, FixtureArchive, IrsaArchive, Observation, cutout_path, mjd_to_date, select_observations, est_cutout_mb
from .services.jobs import JobRunner
from .services.resolve import resolve_name
from .validation import SearchParams, request_id, validate_search

log = logging.getLogger("skydetective")
RID = re.compile(r"^[0-9a-f]{16}$")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,100}\.png$")


class RateLimiter:
    """In-memory sliding window per client. Enough to stop accidental hammering; use a proxy for real abuse."""

    def __init__(self, limit: int = 40, window_s: int = 60):
        self.limit, self.window, self.hits, self.lock = limit, window_s, defaultdict(deque), threading.Lock()

    def check(self, request: Request) -> None:
        ip = request.client.host if request.client else "?"
        now = time.time()
        with self.lock:
            q = self.hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise ApiError("Too many requests. Please wait a minute and try again.", 429)
            q.append(now)


class CombineBody(BaseModel):
    request_id: str
    op: str
    frames: list[str]
    stretch: str = "asinh"


def create_app(settings: Settings | None = None, archive: ArchiveClient | None = None) -> FastAPI:
    s = settings or get_settings()
    s.ensure_dirs()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(s.db_path)
    n = db.fail_interrupted()
    if n:
        log.warning("marked %d interrupted job(s) as failed", n)
    cache = JsonCache(s.cache_dir)
    if archive is None:
        fx = os.getenv("FIXTURE_DIR")
        archive = FixtureArchive(Path(fx), s) if fx else IrsaArchive(s)
    runner = JobRunner(s, db, archive, cache)
    limiter = RateLimiter()

    app = FastAPI(title="Sky Detective API", version=s.pipeline_version,
                  description="Search SPHEREx observations, align them, and return a manifest of evidence. "
                              "Outputs are potential moving sources / brightness changes, never confirmed discoveries.")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST"], allow_headers=["*"])

    # ---- errors: the frontend shows `detail` as plain text -------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_err(_: Request, e: ApiError):
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    @app.exception_handler(RequestValidationError)
    async def _val_err(_: Request, e: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'][1:])}: {err['msg']}" for err in e.errors())
        return JSONResponse({"detail": f"Invalid request. {msg}"}, status_code=422)

    @app.exception_handler(Exception)
    async def _any_err(_: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "Something went wrong on the server. Please retry."}, status_code=500)

    # ---- helpers -----------------------------------------------------------------------------------
    def archive_search(p: SearchParams) -> list[Observation]:
        return _archive_search(archive, cache, s, p)

    def need_request(rid: str) -> dict:
        if not RID.match(rid):
            raise ValidationFailed("Invalid request id.")
        row = db.get(rid)
        if not row:
            raise NotFound("Unknown request. Run a search first.")
        return row

    # ---- endpoints ---------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        ok = os.access(s.data_dir, os.W_OK)
        return {"status": "healthy" if ok else "degraded", "data_release": s.data_release,
                "pipeline_version": s.pipeline_version, "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    @app.get("/api/releases")
    def releases():
        """What data this server is built against. Bump DATA_RELEASE when IRSA publishes a new release:
        request ids, archive searches and processed products are keyed by it, so nothing stale is reused."""
        return {"current": {"data_release": s.data_release, "sia_collection": s.irsa_sia_collection,
                            "pipeline_version": s.pipeline_version}, **db.release_summary()}

    @app.get("/api/resolve")
    def resolve(name: str):
        return resolve_name(name, s, cache)

    @app.get("/api/search")
    def search(request: Request, ra: float, dec: float, radius: float = 0.05,
               start: str | None = None, end: str | None = None, detector: int | None = None):
        limiter.check(request)
        p = validate_search(ra, dec, radius, start, end, detector, s)
        rid = request_id(p, s.data_release, s.pipeline_version)
        row = db.get(rid)
        if not row or row["status"] == "failed":  # new request, or retry of a failed one
            selected = select_observations(archive_search(p), p, s)
            db.create(rid, p.normalized(), [o.to_dict() for o in selected], s.data_release, s.pipeline_version)
            runner.submit(rid)
            row = db.get(rid)
        obs = row["observations"]
        return {
            "request_id": rid,
            "center": {"ra": p.ra, "dec": p.dec},
            "radius_deg": p.radius,
            "status": row["status"],
            "data_release": row["release"],
            "n_observations": len(obs),
            "est_transfer_mb": round(len(obs) * est_cutout_mb(p, s), 1),
            "observations": [
                {"observation_id": o["obs_id"], "detector": o["detector"],
                 "observation_date": mjd_to_date(o["t_mjd"]) if o["t_mjd"] else None,
                 "band_um": [o["em_min_um"], o["em_max_um"]]} for o in obs
            ],
        }

    @app.get("/api/manifest/{rid}")
    def manifest(rid: str, stretch: str = "asinh"):
        if stretch not in STRETCHES:
            raise ValidationFailed(f"stretch must be one of {', '.join(STRETCHES)}.")
        row = need_request(rid)
        base = {"request_id": rid, "status": row["status"], "stage": row["stage"], "progress": row["progress"],
                "frames": [], "difference_frames": []}
        if row["status"] == "failed":
            return {**base, "error": row["error"] or "Processing failed. Please retry."}
        if row["status"] != "complete" or not row["manifest"]:
            return base
        return json.loads(json.dumps(row["manifest"]).replace("{stretch}", stretch))

    @app.get("/api/products/{rid}/{rest:path}")
    def product(rid: str, rest: str):
        """Whitelisted file serving: <rid>/<stretch>/<name>.png or <rid>/combine/<stretch>/<name>.png. Nothing else."""
        parts = rest.split("/")
        ok = RID.match(rid) and ((len(parts) == 2 and parts[0] in STRETCHES) or
                                 (len(parts) == 3 and parts[0] == "combine" and parts[1] in STRETCHES)) and NAME.match(parts[-1])
        if not ok:
            raise NotFound("No such product.")
        path = (s.product_dir / rid / Path(*parts)).resolve()
        if s.product_dir.resolve() not in path.parents or not path.is_file():
            raise NotFound("No such product.")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/api/jobs/combine")
    def combine_job(request: Request, body: CombineBody):
        limiter.check(request)
        row = need_request(body.request_id)
        if row["status"] != "complete":
            raise ApiError("This request is still processing.", 409)
        if body.stretch not in STRETCHES:
            raise ValidationFailed(f"stretch must be one of {', '.join(STRETCHES)}.")
        return combine(body.request_id, body.op, body.frames, row["manifest"], s.product_dir, body.stretch)

    @app.get("/api/spectrum")
    def spectrum(request: Request, request_id: str, ra: float, dec: float, aperture_arcsec: float = 12.0):
        limiter.check(request)
        if not (3.0 <= aperture_arcsec <= 60.0):
            raise ValidationFailed("Aperture must be between 3 and 60 arcsec.")
        row = need_request(request_id)
        if row["status"] != "complete":
            raise ApiError("This request is still processing.", 409)
        prm = SearchParams(**row["params"])
        r = prm.radius
        if abs(ra - prm.ra) * max(abs(math.cos(math.radians(prm.dec))), 0.01) > r or abs(dec - prm.dec) > r:
            raise ValidationFailed("That position is outside the requested field.")
        key = f"spectrum:{s.data_release}:{s.pipeline_version}:{request_id}:{ra:.5f}:{dec:.5f}:{aperture_arcsec:.1f}"
        hit = cache.get(key, 72 * 3600)
        if hit:
            return hit
        points, skipped = [], 0
        for od in row["observations"]:
            obs = Observation.from_dict(od)
            path = cutout_path(obs, prm, s)
            if not path.exists():
                skipped += 1
                continue
            try:
                pt = extract_point(open_cutout(path, obs.obs_id, s.flag_mask_bits, obs.detector), ra, dec, aperture_arcsec)
            except Exception as e:
                log.warning("spectrum: skipping %s: %s", obs.obs_id, e)
                pt = None
            if pt is None:
                skipped += 1
            else:
                points.append(pt)
        points.sort(key=lambda x: x["wavelength_um"])
        out = {"ra": ra, "dec": dec, "aperture_arcsec": aperture_arcsec, "flux_unit": FLUX_UNIT,
               "channels_total": 102, "points": points, "n_skipped": skipped,
               "note": "Points come from different exposures at different times; a moving or variable source can look inconsistent across wavelength."}
        cache.set(key, out)
        return out

    @app.get("/api/catalog/{kind}")
    def catalog(request: Request, kind: str, ra: float, dec: float, radius: float = 0.05):
        limiter.check(request)
        if not (0 <= ra < 360 and -90 <= dec <= 90):
            raise ValidationFailed("RA or Dec out of range.")
        return cone_search(kind, ra, dec, radius, s, cache)

    investigations.register(app, s, limiter, db, runner)
    return app


app = create_app() if os.getenv("SKYDETECTIVE_NO_AUTOAPP") != "1" else None
