"""One place for outbound HTTP: timeouts and retries with backoff on transient failures."""
from __future__ import annotations

import logging
import time

import httpx

from .config import Settings
from .errors import ArchiveUnavailable

log = logging.getLogger("skydetective.http")
_RETRY_STATUS = {429, 500, 502, 503, 504}


USER_AGENT = "SkyDetective/0.1 (NASA Space Apps 2026 project)"


def request_with_retry(method: str, url: str, s: Settings, **kw) -> httpx.Response:
    last: Exception | None = None
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    for attempt in range(s.http_retries):
        try:
            r = httpx.request(method, url, timeout=s.http_timeout_s, follow_redirects=True, headers=headers, **kw)
            if r.status_code in _RETRY_STATUS:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            return r
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
            last = e
            wait = 5 * 2**attempt  # IRSA answers 503 when busy; give it time
            log.warning("request failed (%s), attempt %d/%d, retry in %ds: %s", url[:80], attempt + 1, s.http_retries, wait, e)
            time.sleep(wait)
    raise ArchiveUnavailable() from last
