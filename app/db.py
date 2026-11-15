"""SQLite persistence. One row per request: status, progress, observations, manifest.
A fresh connection per call keeps this safe across the worker threads (WAL mode)."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  params TEXT NOT NULL,
  release TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  status TEXT NOT NULL,          -- pending | processing | complete | failed
  stage TEXT DEFAULT '',
  progress REAL DEFAULT 0,
  error TEXT,
  error_code TEXT,
  observations TEXT NOT NULL,    -- JSON list of archive records
  manifest TEXT,                 -- JSON, png_url holds a {stretch} placeholder
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  completed_at REAL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(requests)")}
            if "error_code" not in cols:  # databases created before error codes existed
                c.execute("ALTER TABLE requests ADD COLUMN error_code TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def get(self, rid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["observations"] = json.loads(d["observations"])
        d["manifest"] = json.loads(d["manifest"]) if d["manifest"] else None
        return d

    def create(self, rid: str, params: dict, observations: list[dict], release: str, pipeline_version: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO requests (id, params, release, pipeline_version, status, stage, progress,"
                " error, observations, manifest, created_at, updated_at) VALUES (?,?,?,?, 'pending','queued',0,NULL,?,NULL,?,?)",
                (rid, json.dumps(params), release, pipeline_version, json.dumps(observations), now, now),
            )

    def update(self, rid: str, **fields: Any) -> None:
        if not fields:
            return
        if "manifest" in fields and fields["manifest"] is not None:
            fields["manifest"] = json.dumps(fields["manifest"])
        if "observations" in fields:
            fields["observations"] = json.dumps(fields["observations"])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE requests SET {cols} WHERE id=?", (*fields.values(), rid))

    def fail_interrupted(self) -> int:
        """On startup, anything still 'processing' was killed by a restart."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE requests SET status='failed', error_code='processing_failed', error='The server restarted during processing. Please retry.'"
                " WHERE status IN ('pending','processing')"
            )
            return cur.rowcount

    def release_summary(self) -> dict[str, Any]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT release, pipeline_version, COUNT(*) n, MAX(updated_at) last FROM requests GROUP BY 1,2"
            ).fetchall()
        return {"cached_requests": [dict(r) for r in rows]}
