# SPDX-License-Identifier: Apache-2.0
"""``/api/v1`` widget builder routes (PLAN §8.5 Flow B): the catalog of what can be shown, a
live preview of a draft widget, and adding, editing and removing a widget on a board.

Every write is a round-trip edit of the board file with the revision the client loaded as a
precondition (409 when someone edited it since), validated before it is written — the same
path as the layout editor. Editing needs ``boards:edit:<name>``; the catalog lists every
resource HUD collects, so it needs ``resources:view``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from ruamel.yaml.comments import CommentedMap

from hud.api import deps
from hud.api.boards import _find, _model
from hud.auth import Principal
from hud.config import ConfigConflictError, ConfigError, LoadedDocument
from hud.config.schemas.board import UnsupportedWidget, Widget
from hud.widgets import ResolvedBoard, ResolvedWidget
from hud.widgets.builder import (
    catalog,
    find,
    merge,
    new_id,
    placement,
    plain,
    to_node,
    widget_entries,
)

router = APIRouter(tags=["builder"])
_WIDGET: TypeAdapter[Widget] = TypeAdapter(Widget)


class Draft(BaseModel):
    widget: dict[str, Any]


class NewWidget(BaseModel):
    revision: str = Field(min_length=12, max_length=12)
    widget: dict[str, Any]


class WidgetChanges(BaseModel):
    revision: str = Field(min_length=12, max_length=12)
    changes: dict[str, Any]  # a key set to null is removed


def _validate(raw: dict[str, Any]) -> Widget:
    """A draft as a widget model: an id and grid are supplied when the form has none yet."""
    data = {"id": "preview", **raw}
    data["grid"] = {"col": 1, "row": 1, **(raw.get("grid") or {})}  # the form sends size only
    try:
        widget = _WIDGET.validate_python(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first["loc"][1:]) or "widget"
        raise HTTPException(status_code=422, detail=f"{where}: {first['msg']}") from exc
    if isinstance(widget, UnsupportedWidget):
        raise HTTPException(
            status_code=422, detail=f"widget type {raw.get('type')!r} is not supported"
        )
    return widget


async def _editable(request: Request, name: str) -> tuple[Principal, LoadedDocument]:
    p = await deps.principal(request)
    found = _find(request, p, name)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {name!r}")
    if not deps.auth(request).authorizer.can_edit_board(p, _model(found)):
        raise HTTPException(status_code=403, detail=f"requires boards:edit:{name}")
    return p, found


@dataclass(frozen=True)
class _Edit:
    revision: str  # the file revision the client loaded
    mutate: Callable[[CommentedMap], None]
    action: str  # the audit row's action
    target: str  # and what it names


async def _write(
    request: Request, p: Principal, found: LoadedDocument, edit: _Edit
) -> ResolvedBoard:
    config = deps.config(request)
    relative = str(found.path.relative_to(config.config_dir))
    try:
        await config.edit_async(relative, edit.mutate, expected_revision=edit.revision)
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409, detail=str(exc), headers={"X-Board-Revision": exc.actual}
        ) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    name = _model(found).metadata.name
    await asyncio.to_thread(
        deps.auth(request).store.audit, p.subject, edit.action, name, "ok", {"widget": edit.target}
    )
    fresh = _find(request, p, name)
    assert fresh is not None
    return await deps.widgets(request).resolve_board(
        _model(fresh),
        datetime.now(UTC),
        revision=fresh.revision,
        page_origin=deps.page_origin(request),
    )


@router.get("/builder/catalog")
async def builder_catalog(request: Request) -> dict[str, Any]:
    """Every provider and kind HUD collects, with its metrics (and units) and attrs."""
    await deps.require(request, "resources:view")
    return {"groups": [asdict(g) for g in catalog(request.app.state.cache)]}


@router.post("/boards/{name}/builder/preview", response_model=ResolvedWidget)
async def preview(request: Request, name: str, body: Draft) -> ResolvedWidget:
    """A draft widget resolved against live data, written nowhere."""
    await _editable(request, name)
    widget = _validate(body.widget)
    return await deps.widgets(request).resolve(
        widget, datetime.now(UTC), page_origin=deps.page_origin(request)
    )


@router.get("/boards/{name}/builder/widgets/{widget_id}")
async def get_widget_yaml(request: Request, name: str, widget_id: str) -> dict[str, Any]:
    """A widget's mapping as written, for the edit form (keys the form does not know are
    kept and shown as YAML-only)."""
    _, found = await _editable(request, name)
    widgets = widget_entries(_raw(found))
    i = find(widgets, widget_id)
    if i is None:
        raise HTTPException(status_code=404, detail=f"no widget {widget_id!r} on board {name!r}")
    return {"widget": plain(widgets[i]), "revision": found.revision}


def _raw(found: LoadedDocument) -> CommentedMap:
    from hud.config.loader import parse_yaml  # noqa: PLC0415 — only the edit form needs it

    return parse_yaml(found.path.read_text(encoding="utf-8"), found.path)


@router.post("/boards/{name}/builder/widgets", response_model=ResolvedBoard, status_code=201)
async def add_widget(request: Request, name: str, body: NewWidget) -> ResolvedBoard:
    """Append a widget, below everything in its section, with an id from its title."""
    p, found = await _editable(request, name)
    _validate(body.widget)
    raw = {k: v for k, v in body.widget.items() if k not in ("id", "grid")}
    size = body.widget.get("grid") or {}

    def mutate(doc: CommentedMap) -> None:
        widgets = widget_entries(doc)
        taken = {str(e.get("id")) for e in widgets if isinstance(e, CommentedMap)}
        wid = new_id(raw.get("title"), str(raw.get("type", "widget")), taken)
        grid = placement(widgets, raw.get("section"), int(size.get("w", 1)), int(size.get("h", 1)))
        entry: dict[str, Any] = {"id": wid, "type": raw["type"]}
        entry.update({k: v for k, v in raw.items() if k != "type"})
        entry["grid"] = {k: v for k, v in grid.items() if not (k in ("w", "h") and v == 1)}
        widgets.append(to_node(entry))

    edit = _Edit(body.revision, mutate, "boards.widget.add", str(raw["type"]))
    return await _write(request, p, found, edit)


@router.patch("/boards/{name}/builder/widgets/{widget_id}", response_model=ResolvedBoard)
async def edit_widget(
    request: Request, name: str, widget_id: str, body: WidgetChanges
) -> ResolvedBoard:
    """Merge a form's changes into one widget; untouched keys keep their text and comments."""
    p, found = await _editable(request, name)
    if "id" in body.changes or "grid" in body.changes:
        raise HTTPException(status_code=422, detail="id and grid are changed by the layout editor")

    def mutate(doc: CommentedMap) -> None:
        widgets = widget_entries(doc)
        i = find(widgets, widget_id)
        if i is None:
            raise ConfigError.single(found.path, f"no widget {widget_id!r}")
        merge(widgets[i], body.changes)

    return await _write(
        request, p, found, _Edit(body.revision, mutate, "boards.widget.edit", widget_id)
    )


@router.delete("/boards/{name}/builder/widgets/{widget_id}", response_model=ResolvedBoard)
async def remove_widget(
    request: Request, name: str, widget_id: str, revision: str
) -> ResolvedBoard:
    p, found = await _editable(request, name)

    def mutate(doc: CommentedMap) -> None:
        widgets = widget_entries(doc)
        i = find(widgets, widget_id)
        if i is None:
            raise ConfigError.single(found.path, f"no widget {widget_id!r}")
        del widgets[i]  # never a slice assignment: that drops ruamel's comments

    return await _write(
        request, p, found, _Edit(revision, mutate, "boards.widget.remove", widget_id)
    )
