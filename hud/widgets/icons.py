# SPDX-License-Identifier: Apache-2.0
"""Service icons, fetched once and served from HUD's own origin.

Names are the ones Homepage labels already use (``homepage.icon``): a dashboard-icons file
(``sonarr.png``, ``plex.svg``), a bare dashboard-icons name (``plex``, taken as SVG), or a
Material Design icon (``mdi-movie-open-star``). Anything else — a path, a URL, Homepage's
own ``/icons/...`` folder — is refused and the SPA draws a letter badge instead.

The backend fetches from two fixed jsDelivr paths only, so the browser never calls a CDN
and a name can never steer the request elsewhere (no SSRF: the host and path prefix are
constants, the name is ``[a-z0-9-]``). Each file is checked by its magic bytes and a size
cap, cached under the data dir, and a 404 is remembered so a missing icon is asked for
once a day, not once per page view. An icon is identity, never status (invariant 9,
amended 2026-09-26): a failed fetch costs a badge, not a tile.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# dashboard-icons (Apache-2.0): https://github.com/homarr-labs/dashboard-icons
DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
# Material Design Icons (Pictogrammers), pinned so a name always means the same glyph.
MDI = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg"

MAX_BYTES = 512 * 1024
MISS_TTL = 24 * 3600  # a 404 is asked for again after a day
ERROR_BACKOFF = 300  # a network error, after five minutes
FETCH_TIMEOUT = 5.0

TYPES = {"png": "image/png", "svg": "image/svg+xml", "webp": "image/webp"}
_STEM = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")


@dataclass(frozen=True)
class IconRef:
    key: str  # cache file name: [a-z0-9-]+.ext, safe as a path component
    url: str
    ext: str


def resolve(name: str) -> IconRef | None:
    """The upstream for a label value, or None when it is not a name HUD will fetch."""
    name = name.strip().lower()
    if name.startswith("mdi:"):  # the board/template spelling (icon: mdi:server)
        name = "mdi-" + name.removeprefix("mdi:")
    if name.startswith("mdi-"):
        stem = name.removeprefix("mdi-")
        if _STEM.fullmatch(stem):
            return IconRef(f"mdi-{stem}.svg", f"{MDI}/{stem}.svg", "svg")
        return None
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "svg"
    if ext in TYPES and _STEM.fullmatch(stem):
        return IconRef(f"{stem}.{ext}", f"{DASHBOARD_ICONS}/{ext}/{stem}.{ext}", ext)
    return None


def canonical(name: str | None) -> str | None:
    """The name as /api/v1/icons expects it (``mdi:server`` → ``mdi-server``), or None
    when it is not one HUD would fetch."""
    if not name:
        return None
    n = name.strip().lower()
    n = "mdi-" + n.removeprefix("mdi:") if n.startswith("mdi:") else n
    return n if resolve(n) else None


def slug(title: str) -> str:
    """A dashboard-icons guess from a display name: "Home Assistant" → "home-assistant"."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _looks_like(body: bytes, ext: str) -> bool:
    if ext == "png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "webp":
        return body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    head = body[:1024].lstrip().lower()
    return head.startswith((b"<svg", b"<?xml")) and b"<svg" in body[:4096].lower()


class IconStore:
    def __init__(self, root: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.root = root
        self._transport = transport
        self._locks: dict[str, asyncio.Lock] = {}
        self._backoff: dict[str, float] = {}

    async def get(self, name: str) -> tuple[bytes, str] | None:
        """(body, content type), or None: refused name, missing upstream, or backing off."""
        ref = resolve(name)
        if ref is None:
            return None
        hit = self._cached(ref)
        if hit is not None or self._known_missing(ref):
            return hit
        lock = self._locks.setdefault(ref.key, asyncio.Lock())
        async with lock:
            hit = self._cached(ref)  # another request may have fetched it meanwhile
            if hit is not None or self._known_missing(ref):
                return hit
            return await self._fetch(ref)

    def _cached(self, ref: IconRef) -> tuple[bytes, str] | None:
        path = self.root / ref.key
        return (path.read_bytes(), TYPES[ref.ext]) if path.is_file() else None

    def _known_missing(self, ref: IconRef) -> bool:
        if self._backoff.get(ref.key, 0.0) > time.monotonic():
            return True
        marker = self.root / f"{ref.key}.missing"
        return marker.is_file() and time.time() - marker.stat().st_mtime < MISS_TTL

    async def _fetch(self, ref: IconRef) -> tuple[bytes, str] | None:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(FETCH_TIMEOUT),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                resp = await client.get(ref.url)
        except httpx.HTTPError as exc:
            self._backoff[ref.key] = time.monotonic() + ERROR_BACKOFF
            log.info("icon %s: %s", ref.key, type(exc).__name__)
            return None
        body = resp.content
        if resp.status_code != 200 or len(body) > MAX_BYTES or not _looks_like(body, ref.ext):
            reason = f"HTTP {resp.status_code}" if resp.status_code != 200 else "not an image"
            log.info("icon %s: %s", ref.key, reason if len(body) <= MAX_BYTES else "too large")
            self._write(f"{ref.key}.missing", b"")
            return None
        self._write(ref.key, body)
        return body, TYPES[ref.ext]

    def _write(self, key: str, body: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{key}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, self.root / key)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


__all__ = ["IconRef", "IconStore", "canonical", "resolve", "slug"]
