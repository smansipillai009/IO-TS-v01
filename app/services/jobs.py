"""Background processing. A small thread pool is enough for the MVP; the Database is the single source of truth
for job state, so swapping this for Celery/RQ later only means replacing submit()."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import Settings
from ..db import Database
from ..cache import JsonCache
from ..errors import ApiError, ArchiveUnavailable, NoCoverage, ValidationFailed
from ..science.pipeline import run_pipeline
from ..validation import SearchParams
from .archive_search import archive_search
from .irsa import ArchiveClient, Observation, select_observations

log = logging.getLogger("skydetective.jobs")


def _code(e: ApiError) -> str:
    """Codes the frontend has titles for (src/app/investigate/page.tsx)."""
    if isinstance(e, NoCoverage):
        return "no_observations"
    if isinstance(e, ArchiveUnavailable):
        return "download_failed"
    if isinstance(e, ValidationFailed):
        return "invalid_request"
    return "processing_failed"


class JobRunner:
    def __init__(self, s: Settings, db: Database, archive: ArchiveClient, cache: JsonCache | None = None):
        self.s, self.db, self.archive, self.cache = s, db, archive, cache or JsonCache(s.cache_dir)
        self.pool = ThreadPoolExecutor(max_workers=s.workers, thread_name_prefix="pipeline")

    def submit(self, rid: str) -> None:
        self.pool.submit(self._run, rid)

    def _run(self, rid: str) -> None:
        row = self.db.get(rid)
        if not row:
            return
        t0 = time.time()
        self.db.update(rid, status="processing", stage="Starting", progress=0.02)
        try:
            p = SearchParams(**row["params"])
            if not row["observations"]:  # created by POST /api/investigations: the archive lookup happens here, visibly
                self.db.update(rid, stage="Finding observations", progress=0.02)
                chosen = select_observations(archive_search(self.archive, self.cache, self.s, p), p, self.s)
                self.db.update(rid, observations=[o.to_dict() for o in chosen])
                obs = chosen
            else:
                obs = [Observation.from_dict(o) for o in row["observations"]]

            def progress(stage: str, frac: float) -> None:
                self.db.update(rid, stage=stage, progress=round(frac, 3))

            manifest = run_pipeline(rid, p, obs, self.s, self.archive, progress)
            self.db.update(rid, status="complete", stage="Done", progress=1.0, manifest=manifest, completed_at=time.time(), error=None)
            log.info("job %s complete in %.1fs (%d frames)", rid, time.time() - t0, len(manifest["frames"]))
        except ApiError as e:
            log.warning("job %s failed: %s", rid, e.detail)
            self.db.update(rid, status="failed", error=e.detail, error_code=_code(e))
        except Exception as e:  # keep the real cause in the log, show a safe message to the user
            log.exception("job %s crashed", rid)
            msg = str(e) if isinstance(e, ValueError) else "Processing failed unexpectedly. Please retry."
            self.db.update(rid, status="failed", error=msg, error_code="processing_failed")
