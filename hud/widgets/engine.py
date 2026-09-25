# SPDX-License-Identifier: Apache-2.0
"""Widget Engine (PLAN.md §4.1, §8): resolve a board's widget bindings against the live
cache (and raw samples for sparklines) into plain payloads the SPA renders verbatim.

Every widget resolves independently and degrades loudly on its own: an unknown uid, an
unsupported type or a blocked iframe becomes that one tile's ``error`` with a state of
``unknown``, never a failed board (invariant 6). The engine never fetches anything from a
provider; the framing probe is the one outbound call, and it is cached.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine

from hud.collector import STATE_SEVERITY, LiveCache, ResourceFilter
from hud.config.schemas import BoardDocument, Widget
from hud.config.schemas.board import (
    EmbedWidget,
    Grid,
    Layout,
    ListWidget,
    MetricWidget,
    ResourceWidget,
    StaticWidget,
    UnsupportedWidget,
)
from hud.config.schemas.settings import parse_duration
from hud.models import Metric, Resource, State, Unit
from hud.widgets.probe import Framing, FramingProber
from hud.widgets.samples import read_samples

# ----------------------------------------------------------------------------- payloads


class FieldValue(BaseModel):
    key: str
    label: str
    value: Any = None
    unit: Unit | None = None


class ResolvedWidget(BaseModel):
    id: str
    type: str
    title: str | None
    grid: Grid
    state: State | None  # None: the widget has no status (static)
    stale: bool = False
    error: str | None = None
    data: dict[str, Any]


class ResolvedBoard(BaseModel):
    name: str
    title: str
    icon: str | None
    layout: Layout
    generation: int
    revision: str  # of the board's YAML file; PATCH must present it (optimistic concurrency)
    resolved_at: datetime
    widgets: list[ResolvedWidget]


class BoardSummary(BaseModel):
    name: str
    title: str
    icon: str | None
    widgets: int
    unsupported: int


# ----------------------------------------------------------------------------- engine


class WidgetEngine:
    def __init__(
        self, cache: LiveCache, engine: Engine | None, prober: FramingProber | None = None
    ) -> None:
        self.cache = cache
        self.db = engine
        self.prober = prober or FramingProber()

    # ------------------------------------------------------------------ boards

    def summary(self, doc: BoardDocument) -> BoardSummary:
        return BoardSummary(
            name=doc.metadata.name,
            title=doc.metadata.title or doc.metadata.name,
            icon=doc.metadata.icon,
            widgets=len(doc.spec.widgets),
            unsupported=len(doc.unsupported_widgets),
        )

    def referenced_uids(self, doc: BoardDocument) -> set[str]:
        """Every resource uid a board would show right now. Used by RBAC to scope the
        ``/resources`` and ``/events`` APIs to what the caller's boards expose."""
        uids: set[str] = set()
        for w in doc.spec.widgets:
            if isinstance(w, ResourceWidget | MetricWidget):
                uids.add(w.source.resource)
            elif isinstance(w, ListWidget):
                sel = w.source.select
                flt = ResourceFilter(
                    provider=_as_list(sel.provider),
                    kind=_as_list(sel.kind),
                    state=_as_list(sel.state),
                    labels=sel.label,
                    attrs=sel.attrs,
                )
                uids.update(r.uid for r in self.cache.resources(flt))
        return uids

    async def resolve_board(
        self,
        doc: BoardDocument,
        now: datetime,
        *,
        revision: str = "",
        page_origin: str | None = None,
    ) -> ResolvedBoard:
        """``page_origin`` is the HUD page asking (``https://hud.example``): whether an
        embed can be framed depends on which page frames it."""
        widgets = await asyncio.gather(
            *(self.resolve(w, now, page_origin=page_origin) for w in doc.spec.widgets)
        )
        return ResolvedBoard(
            name=doc.metadata.name,
            title=doc.metadata.title or doc.metadata.name,
            icon=doc.metadata.icon,
            layout=doc.spec.layout,
            generation=self.cache.generation,
            revision=revision,
            resolved_at=now,
            widgets=list(widgets),
        )

    async def resolve(
        self, w: Widget, now: datetime, *, page_origin: str | None = None
    ) -> ResolvedWidget:
        try:
            return await self._dispatch(w, now, page_origin)
        except Exception as exc:  # a bug in one resolver degrades one tile, never the board
            return _tile(w, State.UNKNOWN, error=f"{type(exc).__name__}: {exc}", data={})

    async def _dispatch(
        self, w: Widget, now: datetime, page_origin: str | None = None
    ) -> ResolvedWidget:
        match w:
            case StaticWidget():
                return self._static(w)
            case ResourceWidget():
                return self._resource(w)
            case ListWidget():
                return self._list(w)
            case MetricWidget():
                return await self._metric(w, now)
            case EmbedWidget():
                return await self._embed(w, page_origin)
            case UnsupportedWidget():
                return _tile(w, None, error=w.reason, data={"reason": w.reason})

    # ------------------------------------------------------------------ types

    def _static(self, w: StaticWidget) -> ResolvedWidget:
        return _tile(
            w,
            None,
            data={"text": w.display.text, "links": [link.model_dump() for link in w.display.links]},
        )

    def _resource(self, w: ResourceWidget) -> ResolvedWidget:
        r = self.cache.resource(w.source.resource)
        if r is None:
            return _tile(
                w,
                State.UNKNOWN,
                error=f"no resource {w.source.resource!r} (is its provider polling?)",
                data={"resource": None, "fields": []},
            )
        fields = [self.field(r, key) for key in w.display.fields]
        return _tile(
            w,
            r.state,
            stale=r.stale,
            data={
                "resource": r.model_dump(mode="json"),
                "fields": [f.model_dump() for f in fields],
            },
        )

    def _list(self, w: ListWidget) -> ResolvedWidget:
        sel = w.source.select
        flt = ResourceFilter(
            provider=_as_list(sel.provider),
            kind=_as_list(sel.kind),
            state=_as_list(sel.state),
            labels=sel.label,
            attrs=sel.attrs,
        )
        items = self.cache.resources(flt)
        for key in reversed(w.source.sort):  # stable sorts applied last-key-first
            items = _sorted(items, key)
        total = len(items)
        if w.source.limit is not None:
            items = items[: w.source.limit]
        worst = max((STATE_SEVERITY[r.state] for r in items), default=0)
        state = next(s for s, sev in STATE_SEVERITY.items() if sev == worst) if items else State.UP
        rows = [
            {
                "uid": r.uid,
                "name": r.name,
                "state": r.state.value,
                "stale": r.stale,
                "links": r.links,
                "fields": [self.field(r, key).model_dump() for key in w.display.fields],
            }
            for r in items
        ]
        return _tile(
            w,
            state,
            stale=any(r.stale for r in items),
            data={"items": rows, "total": total, "empty_text": w.display.empty_text},
        )

    async def _metric(self, w: MetricWidget, now: datetime) -> ResolvedWidget:
        uid, name = w.source.resource, w.source.metric
        r = self.cache.resource(uid)
        m = self.cache.metric(uid, name)
        if r is None or m is None:
            missing = "resource" if r is None else f"metric {name!r}"
            return _tile(
                w,
                State.UNKNOWN,
                error=f"no {missing} for {uid!r}",
                data={"value": None, "unit": None, "ts": None, "sparkline": None},
            )
        state = State.UP
        for t in w.display.thresholds:
            above = t.gte is not None and m.value >= t.gte
            below = t.lte is not None and m.value <= t.lte
            if above or below:
                state = State.DOWN if t.state == "error" else State.DEGRADED
        sparkline = None
        if w.display.sparkline is not None and self.db is not None:
            span = parse_duration(w.display.sparkline.range)
            until = int(now.timestamp())
            points = await asyncio.to_thread(read_samples, self.db, uid, name, until - span, until)
            sparkline = {"range": w.display.sparkline.range, "points": points}
        return _tile(
            w,
            state,
            stale=r.stale,
            data={
                "value": m.value,
                "unit": m.unit.value,
                "ts": m.ts.isoformat(),
                "resource_name": r.name,
                "format": w.display.format.model_dump(),
                "sparkline": sparkline,
            },
        )

    async def _embed(self, w: EmbedWidget, page_origin: str | None = None) -> ResolvedWidget:
        framing: Framing = await self.prober.probe(w.source.url, page_origin)
        data = {
            "url": w.source.url,
            "sandbox": w.source.sandbox,
            "open_in": w.display.open_in,
            "fallback": w.display.fallback,
            "framing": framing.model_dump(),
        }
        if framing.allowed is False:
            return _tile(w, State.DEGRADED, error=f"cannot be framed: {framing.reason}", data=data)
        if framing.allowed is None:
            return _tile(w, State.UNKNOWN, error=framing.reason, data=data)
        return _tile(w, State.UP, data=data)

    # ------------------------------------------------------------------ fields

    def field(self, r: Resource, key: str) -> FieldValue:
        """``name`` / ``state`` / ``kind`` / ``provider`` / ``attrs.x`` / ``links.x`` /
        ``metric.<name>`` → a labelled value. Unknown keys resolve to None, not an error."""
        label = key.rsplit(".", 1)[-1].replace("_", " ")
        head, _, rest = key.partition(".")
        if head == "metric":
            m: Metric | None = self.cache.metric(r.uid, rest)
            if m is None:
                return FieldValue(key=key, label=label, value=None)
            return FieldValue(key=key, label=label, value=m.value, unit=m.unit)
        return FieldValue(key=key, label=label, value=_plain_field(r, head, rest))


# ----------------------------------------------------------------------------- helpers


def _tile(
    w: Widget,
    state: State | None,
    *,
    data: dict[str, Any],
    error: str | None = None,
    stale: bool = False,
) -> ResolvedWidget:
    return ResolvedWidget(
        id=w.id,
        type=w.type,
        title=w.title,
        grid=w.grid,
        state=state,
        stale=stale,
        error=error,
        data=data,
    )


def _as_list[T](v: T | list[T] | None) -> list[T] | None:
    if v is None:
        return None
    return v if isinstance(v, list) else [v]


def _plain_field(r: Resource, head: str, rest: str) -> Any:  # noqa: ANN401
    if head in ("name", "kind", "provider", "uid") and not rest:
        return getattr(r, head)
    if head == "state" and not rest:
        return r.state.value
    if head == "fetched_at" and not rest:
        return r.fetched_at.isoformat()
    if head == "attrs":
        return _dig(r.attrs, rest)
    if head == "links":
        return r.links.get(rest)
    return None


def _dig(node: Any, path: str) -> Any:  # noqa: ANN401
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _sorted(items: list[Resource], key: str) -> list[Resource]:
    field = key.lstrip("-")
    return sorted(items, key=lambda r: _sort_value(r, field), reverse=key.startswith("-"))


def _sort_value(r: Resource, key: str) -> tuple[int, Any]:
    """Sortable tuple; None sorts last regardless of direction by the leading flag."""
    if key == "state_severity":
        return (0, STATE_SEVERITY[r.state])
    if key in ("name", "kind", "provider"):
        return (0, str(getattr(r, key)).lower())
    if key == "state":
        return (0, r.state.value)
    if key.startswith("attrs."):
        v = _dig(r.attrs, key.removeprefix("attrs."))
        return (1, "") if v is None else (0, v if isinstance(v, int | float) else str(v).lower())
    return (1, "")


__all__ = ["BoardSummary", "FieldValue", "ResolvedBoard", "ResolvedWidget", "WidgetEngine"]
