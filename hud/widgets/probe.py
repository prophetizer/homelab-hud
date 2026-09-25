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
from urllib.parse import urlsplit

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
    fetched: Fetched
    expires: float


@dataclass(frozen=True)
class Fetched:
    """What one fetch of an app said. Cached per URL; the verdict is computed per
    requesting page, because whether a page may frame the app depends on that page."""

    xfo: str = ""
    frame_ancestors: str | None = None  # the raw source list, None when absent
    status: int | None = None
    error: str | None = None  # network failure, None when the app answered
    checked_at: float = 0.0

    @classmethod
    def from_headers(cls, headers: httpx.Headers, status: int | None = None) -> Fetched:
        m = _FRAME_ANCESTORS.search(headers.get("content-security-policy", ""))
        return cls(
            xfo=headers.get("x-frame-options", "").strip().upper(),
            frame_ancestors=m.group(1).strip() if m else None,
            status=status,
            checked_at=time.time(),
        )


def verdict_from_headers(
    headers: httpx.Headers, *, url: str | None = None, page_origin: str | None = None
) -> Framing:
    """Decide from response headers alone. Public so the rule set is testable."""
    return evaluate(Fetched.from_headers(headers), url, page_origin)


def evaluate(fetched: Fetched, url: str | None, page_origin: str | None) -> Framing:
    """May ``page_origin`` frame ``url``? ``page_origin`` is the HUD page asking —
    ``https://hud.example`` — and None when it is not known (then only a wildcard
    ``frame-ancestors`` counts as allowed, which is the conservative reading)."""
    if fetched.error is not None:
        reason = f"unreachable: {fetched.error}"
        return Framing(allowed=None, reason=reason, checked_at=fetched.checked_at)
    allowed, reason = _decide(fetched, url, _origin(page_origin) if page_origin else None)
    if fetched.status is not None and fetched.status >= 400:
        reason = f"HTTP {fetched.status}; {reason}"
    return Framing(allowed=allowed, reason=reason, checked_at=fetched.checked_at)


def _decide(fetched: Fetched, url: str | None, page: str | None) -> tuple[bool, str]:
    # Found live: Node-RED on http:// framed from https://hud.… is blocked by the browser as
    # mixed content — a blank frame, exactly what invariant 6 forbids. It goes to fallback.
    if page and url and page.startswith("https://") and url.lower().startswith("http://"):
        return False, (
            "an http:// app cannot be framed inside an https:// page (the browser blocks "
            "mixed content)"
        )
    if fetched.frame_ancestors is not None:  # overrides X-Frame-Options, as in browsers
        sources = fetched.frame_ancestors
        if page is None:
            allowed = any(t.strip("'") == "*" for t in sources.split())
        else:
            allowed = any(_source_matches(t, page, url) for t in sources.split())
        if allowed:
            return True, f"frame-ancestors {sources}" + (f" allows {page}" if page else "")
        # Found live: an app's proxy listed https://hud.… in frame-ancestors, and the old
        # probe — not knowing its own origin — still called it blocked. Say what to add.
        missing = f" does not include {page}; add it at the proxy to allow framing" if page else ""
        return False, f"Content-Security-Policy: frame-ancestors {sources}{missing}"
    if fetched.xfo == "DENY":
        return False, "X-Frame-Options: DENY"
    if fetched.xfo == "SAMEORIGIN":
        same = page is not None and url is not None and _origin(url) == page
        return same, "X-Frame-Options: SAMEORIGIN" + ("" if same else " (a different origin)")
    return True, "no framing restriction header"


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> str:
    """``scheme://host[:port]``, lower-cased, default port omitted."""
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    default = _DEFAULT_PORTS.get(scheme)
    return f"{scheme}://{host}" + (f":{port}" if port and port != default else "")


def _source_matches(token: str, page: str, url: str | None) -> bool:
    """One CSP ``frame-ancestors`` source expression against the framing page's origin."""
    t = token.strip().lower()
    keyword = {
        "*": True,
        "'none'": False,
        "'self'": url is not None and _origin(url) == page,
    }.get(t)
    if keyword is not None:
        return keyword
    if t.endswith(":") and "/" not in t:  # scheme-source, e.g. https:
        return page.startswith(t + "//")
    return _host_source_matches(t, page)


def _host_source_matches(t: str, page: str) -> bool:
    """``[scheme://]host[:port]``, host optionally ``*.``-prefixed; paths are ignored."""
    src = urlsplit(t if "://" in t else f"//{t}")
    p = urlsplit(page)
    # A source may name http and still match https (CSP's scheme upgrade), not the reverse.
    scheme_ok = (
        not src.scheme
        or src.scheme == p.scheme
        or (src.scheme, p.scheme)
        == (
            "http",
            "https",
        )
    )
    host, _, port_text = src.netloc.partition(":")
    page_host = p.hostname or ""
    if host.startswith("*."):
        host_ok = page_host.endswith(host[1:]) and page_host != host[2:]
    else:
        host_ok = host == page_host
    page_port = p.port or _DEFAULT_PORTS.get(p.scheme)
    if port_text == "*":
        port_ok = True
    elif port_text:
        port_ok = port_text.isdigit() and int(port_text) == page_port
    else:
        port_ok = page_port == _DEFAULT_PORTS.get(p.scheme)
    return scheme_ok and host_ok and port_ok


class FramingProber:
    def __init__(self, timeout: float = PROBE_TIMEOUT) -> None:
        self._timeout = timeout
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[Fetched]] = {}

    def cached(self, url: str, page_origin: str | None = None) -> Framing | None:
        """The verdict from a still-fresh fetch, without fetching; None when there is none."""
        entry = self._cache.get(url)
        if entry is None or entry.expires <= time.monotonic():
            return None
        return evaluate(entry.fetched, url, page_origin)

    async def probe(self, url: str, page_origin: str | None = None) -> Framing:
        """Verdict for ``page_origin`` framing ``url``. The fetch is cached per URL and
        shared; the verdict is evaluated for each asking page."""
        return evaluate(await self._fetched(url), url, page_origin)

    async def _fetched(self, url: str) -> Fetched:
        entry = self._cache.get(url)
        if entry and entry.expires > time.monotonic():
            return entry.fetched
        task = self._inflight.get(url)
        if task is None:
            task = asyncio.create_task(self._fetch(url))
            self._inflight[url] = task
        try:
            return await task
        finally:
            self._inflight.pop(url, None)

    async def _fetch(self, url: str) -> Fetched:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), follow_redirects=True, max_redirects=5
            ) as client:
                resp = await client.get(url, headers={"Accept": "text/html"})
            fetched = Fetched.from_headers(resp.headers, status=resp.status_code)
            ttl = PROBE_TTL
        except httpx.HTTPError as exc:
            fetched = Fetched(error=str(exc), checked_at=time.time())
            ttl = FAILURE_TTL
        self._cache[url] = _Entry(fetched, time.monotonic() + ttl)
        return fetched

    def invalidate(self, url: str | None = None) -> None:
        if url is None:
            self._cache.clear()
        else:
            self._cache.pop(url, None)
