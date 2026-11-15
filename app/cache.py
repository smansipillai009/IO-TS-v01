"""Tiny JSON file cache with TTL. Used for archive searches, catalogs, name lookups and spectra.
Keys always include the data release and pipeline version so a new release never reads old results."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha256(key.encode()).hexdigest()[:40] + ".json")

    def get(self, key: str, ttl_s: float) -> Any | None:
        p = self._path(key)
        try:
            if time.time() - p.stat().st_mtime > ttl_s:
                return None
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def get_stale(self, key: str) -> Any | None:
        """Ignore the TTL. Used as a last resort when the archive is down."""
        try:
            return json.loads(self._path(key).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(p)
