# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/icons/{name}`` — service icons from HUD's own origin (see hud.widgets.icons).

Signed-in callers only: the route makes an outbound fetch on a cache miss, and anonymous
traffic has no reason to trigger one. SVG is served locked down — no scripts, no loads,
sandboxed — so an icon opened directly is an image, never a page (the SPA only ever uses
it in ``<img>``, where scripts do not run anyway).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request, Response

from hud.api import deps

if TYPE_CHECKING:
    from hud.widgets.icons import IconStore

router = APIRouter(tags=["icons"])

HEADERS = {
    "Cache-Control": "private, max-age=604800",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


@router.get("/icons/{name}")
async def get_icon(request: Request, name: str) -> Response:
    await deps.principal(request)
    store = cast("IconStore", request.app.state.icons)
    found = await store.get(name)
    if found is None:
        raise HTTPException(status_code=404, detail="no icon")
    body, media_type = found
    return Response(content=body, media_type=media_type, headers=HEADERS)
