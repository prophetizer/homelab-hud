# SPDX-License-Identifier: Apache-2.0
"""Provider-relative images (posters, thumbnails), fetched with the provider's own client.

A template maps ``attrs.image`` to a path on the provider (Plex ``/library/metadata/…/
thumb/…``, Emby ``/Items/…/Images/Primary``). The browser never sees that path or the
credential that fetches it: it asks HUD for the resource's image by uid, and HUD fetches
the path through the provider's client — pinned to its base URL, redirects off, its own
auth — so the token stays server-side (invariant 3) and a path can never steer the
request to another host.
"""

from __future__ import annotations

import re

import httpx

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ACCEPT = "image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.1"

# Relative to the base URL: one leading slash (not two, which would name a host), no
# whitespace, backslash or fragment; the query is allowed (Emby's ?maxHeight=240).
_PATH = re.compile(r"/(?!/)[^\s\\#]{0,511}")


def safe_image_path(path: object) -> bool:
    if not isinstance(path, str) or not _PATH.fullmatch(path) or "://" in path:
        return False
    return ".." not in path.split("?", 1)[0].split("/")


def sniff(body: bytes) -> str | None:
    """The image type from its magic bytes; None for anything else (SVG included: a
    poster is a raster, and a raster cannot carry script)."""
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


async def fetch_image(client: httpx.AsyncClient, path: str) -> tuple[bytes, str] | None:
    """GET ``path`` on the client's base URL; the body and its type, or None."""
    if not safe_image_path(path):
        return None
    body = bytearray()
    try:
        async with client.stream("GET", path, headers={"Accept": ACCEPT}) as resp:
            if resp.status_code != 200:
                return None
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > MAX_IMAGE_BYTES:
                    return None
    except httpx.HTTPError:
        return None  # never log the URL: a query-auth token rides on it
    kind = sniff(bytes(body))
    return (bytes(body), kind) if kind else None


__all__ = ["MAX_IMAGE_BYTES", "fetch_image", "safe_image_path", "sniff"]
