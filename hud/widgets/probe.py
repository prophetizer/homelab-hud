# SPDX-License-Identifier: Apache-2.0
"""Framing probe for ``embed`` widgets (PLAN.md §8.4).

Many apps refuse to be framed via ``X-Frame-Options`` or a CSP ``frame-ancestors``. The
probe fetches the target once, reads those headers, and caches the verdict so the widget
can render an honest card instead of an empty grey box. Unreachable is reported as
unreachable — never guessed either way.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

PROBE_TIMEOUT = 4.0
PROBE_TTL = 600.0  # seconds a verdict is trusted
FAILURE_TTL = 60.0  # retry sooner after a network failure
_FRAME_ANCESTORS = re.compile(r"frame-ancestors\s+([^;]+)", re.IGNORECASE)


class Framing(BaseModel):
    allowed: bool | None  # None = could not determine
    reason: str
    checked_at: float


@dataclass
class _Entry:
    framing: Framing
    expires: float


def verdict_from_headers(headers: httpx.Headers) -> Framing:
    """Decide from response headers alone. Public so the SPA-side rule set is testable."""
    now = time.time()
    xfo = headers.get("x-frame-options", "").strip().upper()
    if xfo in ("DENY", "SAMEORIGIN"):
        return Framing(allowed=False, reason=f"X-Frame-Options: {xfo}", checked_at=now)
    csp = headers.get("content-security-policy", "")
    m = _FRAME_ANCESTORS.search(csp)
    if m:
        sources = m.group(1).strip()
        tokens = {t.strip("'").lower() for t in sources.split()}
        if "*" in tokens:
            return Framing(allowed=True, reason="frame-ancestors *", checked_at=now)
        # 'self', 'none' or an explicit origin list we are not in: HUD's own origin is
        # unknown here, so anything but a wildcard is treated as blocked and said so.
        reason = f"Content-Security-Policy: frame-ancestors {sources}"
        return Framing(allowed=False, reason=reason, checked_at=now)
    return Framing(allowed=True, reason="no framing restriction header", checked_at=now)


class FramingProber:
    def __init__(self, timeout: float = PROBE_TIMEOUT) -> None:
        self._timeout = timeout
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[Framing]] = {}

    def cached(self, url: str) -> Framing | None:
        entry = self._cache.get(url)
        if entry and entry.expires > time.monotonic():
            return entry.framing
        return None

    async def probe(self, url: str) -> Framing:
        hit = self.cached(url)
        if hit is not None:
            return hit
        task = self._inflight.get(url)
        if task is None:
            task = asyncio.create_task(self._probe(url))
            self._inflight[url] = task
        try:
            return await task
        finally:
            self._inflight.pop(url, None)

    async def _probe(self, url: str) -> Framing:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), follow_redirects=True, max_redirects=5
            ) as client:
                resp = await client.get(url, headers={"Accept": "text/html"})
            framing = verdict_from_headers(resp.headers)
            if resp.status_code >= 400:
                framing = Framing(
                    allowed=framing.allowed,
                    reason=f"HTTP {resp.status_code}; {framing.reason}",
                    checked_at=framing.checked_at,
                )
            ttl = PROBE_TTL
        except httpx.HTTPError as exc:
            framing = Framing(allowed=None, reason=f"unreachable: {exc}", checked_at=time.time())
            ttl = FAILURE_TTL
        self._cache[url] = _Entry(framing, time.monotonic() + ttl)
        return framing

    def invalidate(self, url: str | None = None) -> None:
        if url is None:
            self._cache.clear()
        else:
            self._cache.pop(url, None)
