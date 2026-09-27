# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/images/{uid}`` — a resource's poster or thumbnail (see hud.providers.images).

The caller names a resource, never a URL. The resource must be one their boards show (the
same scope as ``/resources``); its ``attrs.image`` path is fetched by its own provider.
Results are held in memory briefly — a poster does not change during a stream — and a
miss is remembered for a minute so a missing image is not re-asked on every poll.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response

from hud.api import deps

router = APIRouter(tags=["images"])

HIT_TTL = 600.0
MISS_TTL = 60.0
MAX_ENTRIES = 64

HEADERS = {
    "Cache-Control": "private, max-age=600",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}

Entry = tuple[float, bytes, str] | tuple[float, None, None]


class ImageCache:
    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, str], Entry] = OrderedDict()

    def get(self, key: tuple[str, str]) -> Entry | None:
        entry = self._entries.get(key)
        if entry is None or entry[0] < time.monotonic():
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, key: tuple[str, str], body: bytes | None, kind: str | None) -> None:
        ttl = HIT_TTL if body is not None else MISS_TTL
        entry: Entry = (
            (time.monotonic() + ttl, body, kind)
            if body is not None and kind is not None
            else (time.monotonic() + ttl, None, None)
        )
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > MAX_ENTRIES:
            self._entries.popitem(last=False)


@router.get("/images/{uid:path}")
async def get_image(request: Request, uid: str) -> Response:
    p = await deps.principal(request)
    visible = deps.visible_uids(request, p)
    r = deps.cache(request).resource(uid)
    path = r.attrs.get("image") if r is not None else None
    # One answer for "not yours", "gone" and "no image": nothing to learn from the status.
    if r is None or (visible is not None and uid not in visible) or not isinstance(path, str):
        raise HTTPException(status_code=404, detail="no image")
    cache = cast("ImageCache", request.app.state.images)
    key = (uid, path)
    entry = cache.get(key)
    if entry is None:
        provider = deps.registry(request).get(r.provider)
        found = await provider.fetch_image(path) if provider is not None else None
        cache.put(key, *(found or (None, None)))
        entry = cache.get(key)
    if entry is None or entry[1] is None or entry[2] is None:
        raise HTTPException(status_code=404, detail="no image")
    return Response(content=entry[1], media_type=entry[2], headers=HEADERS)
