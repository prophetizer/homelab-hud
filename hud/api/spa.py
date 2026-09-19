# SPDX-License-Identifier: Apache-2.0
"""Serve the built SPA from the same port as the API (PLAN.md §4.2).

Hashed assets under ``/assets`` are immutable; ``index.html`` is always ``no-store`` so a
new image is picked up on the next load. There is no service worker, ever (invariant 2).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

INDEX_HEADERS = {"Cache-Control": "no-store"}
NOT_BUILT = (
    "HUD frontend is not built. Run `cd web && npm install && npm run build`, "
    "or set HUD_STATIC_DIR to a built copy.\n"
)


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503, headers=INDEX_HEADERS)
        # Real files at the root (favicon, manifest) are served as-is; everything else
        # is the client-side router's problem.
        candidate = (static_dir / path).resolve() if path else index
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers=INDEX_HEADERS)
