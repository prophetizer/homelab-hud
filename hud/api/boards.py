# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/boards`` — list, resolve, and the layout editor's write path (Appendix A).

A board the caller cannot view never enters a response: the list omits it and the detail
route answers 404, not 403, so its existence is not disclosed (PLAN.md §10.2). Editing
needs ``boards:edit:<name>`` on top of visibility.

``PATCH`` carries widget placements keyed by widget id, not a positional JSON Patch: ids
are stable across hand edits, array indices are not. The write goes through the
round-trip editor (§8.3) so comments and key order survive, and it is refused with 409
when the file on disk is no longer the revision the client loaded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ruamel.yaml.comments import CommentedMap

from hud.api import deps
from hud.auth import Principal
from hud.config import ConfigConflictError, ConfigError, LoadedDocument
from hud.config.schemas import BoardDocument
from hud.config.schemas.board import MAX_CHART_RANGE, Grid
from hud.config.schemas.duration import parse_duration
from hud.widgets import BoardSummary, ResolvedBoard, ResolvedWidget

router = APIRouter(tags=["boards"])


class BoardList(BaseModel):
    boards: list[BoardSummary]


class Placement(BaseModel):
    id: str
    grid: Grid


class BoardPatch(BaseModel):
    revision: str = Field(min_length=12, max_length=12)
    widgets: list[Placement] = Field(min_length=1)


def _find(request: Request, p: Principal, name: str) -> LoadedDocument | None:
    files = deps.visible_board_files(request, p)
    return next((d for d in files if _model(d).metadata.name == name), None)


def _model(d: LoadedDocument) -> BoardDocument:
    assert isinstance(d.model, BoardDocument)
    return d.model


@router.get("/boards", response_model=BoardList)
async def list_boards(request: Request) -> BoardList:
    p = await deps.principal(request)
    engine = deps.widgets(request)
    return BoardList(boards=[engine.summary(d) for d in deps.visible_boards(request, p)])


@router.get("/boards/{name}", response_model=ResolvedBoard)
async def get_board(request: Request, name: str) -> ResolvedBoard:
    p = await deps.principal(request)
    found = _find(request, p, name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {name!r}")
    return await deps.widgets(request).resolve_board(
        _model(found),
        datetime.now(UTC),
        revision=found.revision,
        page_origin=deps.page_origin(request),
    )


@router.get("/boards/{name}/widgets/{widget_id}/expanded", response_model=ResolvedWidget)
async def get_expanded_widget(request: Request, name: str, widget_id: str) -> ResolvedWidget:
    """One tile as its detail view shows it (every row, a day of trend). The board must be
    one the caller can see, exactly as for the board itself."""
    p = await deps.principal(request)
    found = _find(request, p, name)
    widget = (
        next((w for w in _model(found).spec.widgets if w.id == widget_id), None) if found else None
    )
    if widget is None:
        raise HTTPException(status_code=404, detail=f"no widget {widget_id!r} on board {name!r}")
    engine = deps.widgets(request)
    return await engine.resolve(
        engine.expand(widget), datetime.now(UTC), page_origin=deps.page_origin(request)
    )


@router.get("/boards/{name}/widgets/{widget_id}", response_model=ResolvedWidget)
async def get_widget(
    request: Request, name: str, widget_id: str, range: str | None = None
) -> ResolvedWidget:
    """One tile, resolved on its own — a chart over another range (any duration up to
    30d). The board must be one the caller can see, exactly as for the board itself."""
    p = await deps.principal(request)
    if range is not None:
        try:
            seconds = parse_duration(range)
        except ValueError:
            seconds = 0
        if not 0 < seconds <= MAX_CHART_RANGE:
            raise HTTPException(status_code=422, detail="range must be a duration up to 30d")
    found = _find(request, p, name)
    widget = (
        next((w for w in _model(found).spec.widgets if w.id == widget_id), None) if found else None
    )
    if widget is None:
        raise HTTPException(status_code=404, detail=f"no widget {widget_id!r} on board {name!r}")
    return await deps.widgets(request).resolve(
        widget, datetime.now(UTC), page_origin=deps.page_origin(request), chart_range=range
    )


@router.patch(
    "/boards/{name}",
    response_model=ResolvedBoard,
    responses={
        409: {"description": "board changed since it was loaded; X-Board-Revision is current"}
    },
)
async def patch_board(request: Request, name: str, body: BoardPatch) -> ResolvedBoard:
    p = await deps.principal(request)
    found = _find(request, p, name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {name!r}")
    board = _model(found)
    if not deps.auth(request).authorizer.can_edit_board(p, board):
        raise HTTPException(status_code=403, detail=f"requires boards:edit:{name}")
    known = {w.id for w in board.spec.widgets}
    unknown = sorted({pl.id for pl in body.widgets} - known)
    if unknown:
        raise HTTPException(status_code=422, detail=f"no such widget(s): {', '.join(unknown)}")
    ids = [pl.id for pl in body.widgets]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="a widget id is listed twice")

    config = deps.config(request)
    relative = str(found.path.relative_to(config.config_dir))
    placements = {pl.id: pl.grid for pl in body.widgets}
    try:
        await config.edit_async(
            relative, lambda doc: _apply(doc, placements), expected_revision=body.revision
        )
    except ConfigConflictError as exc:
        # Someone edited the file (by hand, or in another tab) after this client loaded it.
        raise HTTPException(
            status_code=409, detail=str(exc), headers={"X-Board-Revision": exc.actual}
        ) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store = deps.auth(request).store
    await asyncio.to_thread(
        store.audit, p.subject, "boards.layout", name, "ok", {"widgets": sorted(placements)}
    )
    fresh = _find(request, p, name)
    assert fresh is not None  # we just wrote it; visibility is unchanged by a grid edit
    return await deps.widgets(request).resolve_board(
        _model(fresh),
        datetime.now(UTC),
        revision=fresh.revision,
        page_origin=deps.page_origin(request),
    )


def _apply(doc: CommentedMap, placements: dict[str, Grid]) -> None:
    """Set ``grid`` on each addressed widget in place. Existing ``grid`` mappings keep
    their style (flow or block); ``w``/``h`` are written only when they are not the
    default or were already present, so a minimal file stays minimal."""
    spec = doc.get("spec")
    widgets = spec.get("widgets") if isinstance(spec, CommentedMap) else None
    if not widgets:
        return
    for entry in widgets:
        if not isinstance(entry, CommentedMap):
            continue
        grid = placements.get(entry.get("id"))
        if grid is None:
            continue
        target = entry.get("grid")
        if not isinstance(target, CommentedMap):
            target = CommentedMap()
            target.fa.set_flow_style()
            entry["grid"] = target
        target["col"] = grid.col
        target["row"] = grid.row
        for key, value in (("w", grid.w), ("h", grid.h)):
            if key in target or value != 1:
                target[key] = value
