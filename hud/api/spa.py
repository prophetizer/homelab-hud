# SPDX-License-Identifier: Apache-2.0
"""Serve the built SPA from the same port as the API (PLAN.md §4.2).

Hashed assets under ``/assets`` are immutable; ``index.html`` is always ``no-store`` so a
new image is picked up on the next load. There is no service worker, ever (invariant 2).

``index.html`` also carries a ``frame-src`` allowlist generated from the embed widgets in
config (PLAN.md §8.4): a compromised YAML cannot frame an arbitrary origin without also
appearing in that list, and the list is only ever what the boards declare.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from hud.config import ConfigManager
from hud.config.schemas import BoardDocument, EmbedWidget

INDEX_HEADERS = {"Cache-Control": "no-store"}


def frame_src(config: ConfigManager | None) -> str:
    """``frame-src`` directive value: 'self' plus every embed origin declared in a board."""
    origins: set[str] = set()
    if config is not None:
        for d in config.snapshot.documents:
            if not isinstance(d.model, BoardDocument):
                continue
            for w in d.model.spec.widgets:
                if isinstance(w, EmbedWidget):
                    parts = urlsplit(w.source.url)
                    if parts.scheme and parts.netloc:
                        origins.add(f"{parts.scheme}://{parts.netloc}")
    return " ".join(["'self'", *sorted(origins)])


def index_headers(config: ConfigManager | None) -> dict[str, str]:
    return {**INDEX_HEADERS, "Content-Security-Policy": f"frame-src {frame_src(config)}"}


def with_theme(html: str, config: ConfigManager | None) -> str:
    """The instance's theme (settings.theme) on ``<html>``, so the first paint is already
    right; a viewer's own choice, kept in their browser, overrides it in the SPA. The
    value is a validated literal (dark | light | auto), never user text."""
    theme = "dark"
    themepark = False
    if config is not None:
        try:
            theme = config.snapshot.settings.spec.theme
            themepark = config.snapshot.settings.spec.theme_park is not None
        except RuntimeError:  # no config loaded yet: the default is fine
            theme = "dark"
    # data-themepark: /api/v1/theme.css (a theme.park palette) applies; see hud/theming.py.
    attrs = f'data-theme="{theme}"' + (" data-themepark" if themepark else "")
    return html.replace("<html ", f"<html {attrs} ", 1)


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
        config = getattr(request.app.state, "config", None)
        return Response(
            with_theme(index.read_text(), config),
            media_type="text/html",
            headers=index_headers(config),
        )
