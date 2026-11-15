"""Cached archive search shared by /api/search and the background job. Cached 6 h; a stale copy is served if the archive is down."""
from __future__ import annotations

import logging

from ..cache import JsonCache
from ..config import Settings
from ..errors import ArchiveUnavailable
from ..validation import SearchParams
from .irsa import ArchiveClient, Observation

log = logging.getLogger("skydetective.search")


def archive_search(archive: ArchiveClient, cache: JsonCache, s: Settings, p: SearchParams) -> list[Observation]:
    key = f"search:{s.data_release}:{p.ra:.5f}:{p.dec:.5f}:{p.radius:.4f}"
    hit = cache.get(key, 6 * 3600)
    if hit is not None:
        return [Observation.from_dict(d) for d in hit]
    try:
        obs = archive.search(p)
    except ArchiveUnavailable:
        stale = cache.get_stale(key)  # better an old answer than a broken demo
        if stale is None:
            raise
        log.warning("archive down; serving stale search for %s", key)
        return [Observation.from_dict(d) for d in stale]
    cache.set(key, [o.to_dict() for o in obs])
    return obs
