# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/apps`` — the workspace pane's launcher list (PLAN.md §8.4, Phase 1 "Workspace
shell" milestone).

An *app* is an ``embed`` widget with ``display.open_in: workspace``. The list is derived
from the boards the caller can view, so it inherits their visibility rule and needs no
grant of its own; an app on a hidden board does not exist for that caller. Each entry
carries the framing verdict from the probe, so the shell can decide between the iframe
and the honest fallback card before it navigates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hud.api import deps
from hud.auth import Principal
from hud.config.schemas import BoardDocument, EmbedWidget
from hud.models import State
from hud.widgets.icons import canonical, slug
from hud.widgets.probe import Framing

router = APIRouter(tags=["apps"])


class App(BaseModel):
    board: str
    board_title: str
    widget: str
    title: str
    url: str
    sandbox: Literal["strict", "relaxed"]
    fallback: Literal["card", "new_tab"]
    state: State
    error: str | None
    framing: Framing
    # widget.icon, else a dashboard-icons guess from the title ("Home Assistant" →
    # home-assistant). A wrong guess is one remembered 404 and a letter badge.
    icon: str | None = None


class AppList(BaseModel):
    apps: list[App]


def _workspace_embeds(board: BoardDocument) -> list[EmbedWidget]:
    return [
        w
        for w in board.spec.widgets
        if isinstance(w, EmbedWidget) and w.display.open_in == "workspace"
    ]


async def _app(request: Request, board: BoardDocument, w: EmbedWidget) -> App:
    resolved = await deps.widgets(request).resolve(
        w, datetime.now(UTC), page_origin=deps.page_origin(request)
    )
    return App(
        board=board.metadata.name,
        board_title=board.metadata.title or board.metadata.name,
        widget=w.id,
        title=w.title or w.id,
        url=w.source.url,
        sandbox=w.source.sandbox,
        fallback=w.display.fallback,
        state=resolved.state or State.UNKNOWN,
        error=resolved.error,
        framing=Framing.model_validate(resolved.data["framing"]),
        icon=canonical(w.icon) if w.icon else canonical(slug(w.title or w.id)),
    )


async def _visible(request: Request, p: Principal) -> list[tuple[BoardDocument, EmbedWidget]]:
    return [(b, w) for b in deps.visible_boards(request, p) for w in _workspace_embeds(b)]


@router.get("/apps", response_model=AppList)
async def list_apps(request: Request) -> AppList:
    p = await deps.principal(request)
    return AppList(apps=[await _app(request, b, w) for b, w in await _visible(request, p)])


@router.get("/apps/{board}/{widget}", response_model=App)
async def get_app(request: Request, board: str, widget: str) -> App:
    p = await deps.principal(request)
    for b, w in await _visible(request, p):
        if b.metadata.name == board and w.id == widget:
            return await _app(request, b, w)
    raise HTTPException(status_code=404, detail=f"no workspace app {board}/{widget}")
