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
import re
import statistics
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import Engine

from hud.collector import STATE_SEVERITY, LiveCache, ResourceFilter
from hud.collector.availability import effective_state
from hud.config.schemas import BoardDocument, Widget
from hud.config.schemas.board import (
    CHART_RANGES,
    BarsWidget,
    ChartWidget,
    EmbedWidget,
    Grid,
    HeatmapWidget,
    HeroStat,
    IncidentsWidget,
    Layout,
    ListSource,
    ListWidget,
    MetricWidget,
    ResourceWidget,
    Sparkline,
    StaticWidget,
    StatusWidget,
    Threshold,
    UnsupportedWidget,
    UptimeWidget,
)
from hud.config.schemas.duration import parse_duration
from hud.models import Metric, Resource, State, Unit
from hud.widgets.icons import canonical as canonical_icon
from hud.widgets.icons import resolve as resolve_icon
from hud.widgets.icons import slug as icon_slug
from hud.widgets.probe import Framing, FramingProber
from hud.widgets.samples import read_samples
from hud.widgets.series import bucket_axis, bucketed, heat, read_points
from hud.widgets.uptime import RANGES, cells, day_cells, ratio, read_spans, tally

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
    icon: str | None = None  # header icon: widget.icon, or its single provider's


class ResolvedBoard(BaseModel):
    name: str
    title: str
    icon: str | None
    layout: Layout
    generation: int
    revision: str  # of the board's YAML file; PATCH must present it (optimistic concurrency)
    resolved_at: datetime
    widgets: list[ResolvedWidget]
    # Distinct resources the board shows, by state: the summary bar's counts.
    summary: dict[str, int] = {}


class BoardSummary(BaseModel):
    name: str
    title: str
    icon: str | None
    widgets: int
    unsupported: int
    # Workspace apps (embed + open_in: workspace). A board made only of apps is an app
    # category, not a dashboard; the sidebar needs to know that without waiting for
    # /apps, which probes every app's framing first.
    apps: int = 0
    # The worst state among the resources the board shows, and how many are down: the
    # sidebar's per-board dot and count. None for a board that shows no resources.
    state: State | None = None
    down: int = 0


# ----------------------------------------------------------------------------- engine


class WidgetEngine:
    def __init__(
        self,
        cache: LiveCache,
        engine: Engine | None,
        prober: FramingProber | None = None,
        timezone: Callable[[], str] | None = None,
    ) -> None:
        self.cache = cache
        self.db = engine
        self.prober = prober or FramingProber()
        # Where "a day" begins, for calendar views: settings.timezone, read live so a
        # config reload takes effect without wiring.
        self._timezone = timezone

    @property
    def tz(self) -> tzinfo:
        name = self._timezone() if self._timezone else "UTC"
        return UTC if name == "UTC" else ZoneInfo(name)

    # ------------------------------------------------------------------ boards

    def summary(self, doc: BoardDocument) -> BoardSummary:
        shown = [r for uid in self.referenced_uids(doc) if (r := self.cache.resource(uid))]
        return BoardSummary(
            state=_worst(shown) if shown else None,
            down=sum(1 for r in shown if r.state is State.DOWN),
            name=doc.metadata.name,
            title=doc.metadata.title or doc.metadata.name,
            icon=doc.metadata.icon,
            widgets=len(doc.spec.widgets),
            unsupported=len(doc.unsupported_widgets),
            apps=sum(
                1
                for w in doc.spec.widgets
                if isinstance(w, EmbedWidget) and w.display.open_in == "workspace"
            ),
        )

    def referenced_uids(self, doc: BoardDocument) -> set[str]:
        """Every resource uid a board would show right now. Used by RBAC to scope the
        ``/resources`` and ``/events`` APIs to what the caller's boards expose."""
        uids: set[str] = set()
        for w in doc.spec.widgets:
            if isinstance(w, ResourceWidget | MetricWidget):
                uids.add(w.source.resource)
                if isinstance(w, MetricWidget) and w.display.total is not None:
                    uids.add(w.display.total.resource or w.source.resource)
                if isinstance(w, ResourceWidget):
                    uids.update(stat.resource for stat in w.display.stats)
            elif isinstance(w, ListWidget | BarsWidget | StatusWidget | IncidentsWidget):
                uids.update(r.uid for r in self.cache.resources(_filter(w.source)))
            elif isinstance(w, UptimeWidget):
                if w.source.resource:
                    uids.add(w.source.resource)
                else:
                    uids.update(r.uid for r in self.cache.resources(_filter(w.source)))
            elif isinstance(w, ChartWidget):
                uids.update(uid for uid, _, _ in self._chart_series(w))
            elif isinstance(w, HeatmapWidget):
                uids.add(w.source.resource)
            if isinstance(w, ListWidget):
                uids.update(stat.resource for stat in w.display.stats)
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
        summary: dict[str, int] = {}
        for uid in self.referenced_uids(doc):
            r = self.cache.resource(uid)
            if r is not None:
                summary[r.state.value] = summary.get(r.state.value, 0) + 1
        return ResolvedBoard(
            name=doc.metadata.name,
            title=doc.metadata.title or doc.metadata.name,
            icon=doc.metadata.icon,
            layout=doc.spec.layout,
            generation=self.cache.generation,
            revision=revision,
            resolved_at=now,
            widgets=list(widgets),
            summary=summary,
        )

    async def resolve(
        self,
        w: Widget,
        now: datetime,
        *,
        page_origin: str | None = None,
        chart_range: str | None = None,
    ) -> ResolvedWidget:
        """``chart_range`` shows a chart over another of its ranges; other types ignore it."""
        try:
            if chart_range is not None and isinstance(w, ChartWidget):
                resolved = await self._chart(w, now, chart_range)
            else:
                resolved = await self._dispatch(w, now, page_origin)
        except Exception as exc:  # a bug in one resolver degrades one tile, never the board
            resolved = _tile(w, State.UNKNOWN, error=f"{type(exc).__name__}: {exc}", data={})
        resolved.icon = self._widget_icon(w, resolved)
        return resolved

    def _widget_icon(self, w: Widget, resolved: ResolvedWidget) -> str | None:
        """widget.icon ("none" hides it), else the icon of the one provider it shows."""
        if w.icon is not None:
            return None if w.icon.strip().lower() == "none" else canonical_icon(w.icon)
        providers: set[str] = set()
        single = _single_resource(w)
        if single is not None:
            providers = {single.split(":", 1)[0]}
        elif isinstance(w, ChartWidget):
            providers = {str(s["uid"]).split(":", 1)[0] for s in resolved.data.get("series", [])}
        elif isinstance(w, ListWidget | BarsWidget | UptimeWidget | StatusWidget | IncidentsWidget):
            sel = _as_list(w.source.select.provider)
            data = resolved.data
            rows = (
                data.get("items")
                or data.get("bars")
                or data.get("rows")
                or data.get("problems")
                or []
            )
            providers = set(sel) if sel else {str(i["uid"]).split(":", 1)[0] for i in rows}
        return canonical_icon(providers.pop()) if len(providers) == 1 else None

    async def _dispatch(
        self, w: Widget, now: datetime, page_origin: str | None = None
    ) -> ResolvedWidget:
        pending: Coroutine[Any, Any, ResolvedWidget]
        match w:
            case MetricWidget():
                pending = self._metric(w, now)
            case EmbedWidget():
                pending = self._embed(w, page_origin)
            case UptimeWidget():
                pending = self._uptime(w, now)
            case ListWidget():
                pending = self._list_with_uptime(w, now)
            case IncidentsWidget():
                pending = self._incidents(w, now)
            case ChartWidget():
                pending = self._chart(w, now)
            case HeatmapWidget():
                pending = self._heatmap(w, now)
            case _:
                return self._resolve_sync(w)
        return await pending

    def _resolve_sync(self, w: Widget) -> ResolvedWidget:
        match w:
            case StaticWidget():
                return self._static(w)
            case ResourceWidget():
                return self._resource(w)
            case BarsWidget():
                return self._bars(w)
            case StatusWidget():
                return self._status(w)
            case _:
                reason = (
                    w.reason if isinstance(w, UnsupportedWidget) else f"no resolver for {w.type}"
                )
                return _tile(w, None, error=reason, data={"reason": reason})

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
                "style": w.display.style,
                "stats": [self.stat(s) for s in w.display.stats],
            },
        )

    def stat(self, s: HeroStat) -> dict[str, Any]:
        """One hero reading; a missing metric is a reading with no value, not an error."""
        m = self.cache.metric(s.resource, s.metric)
        return {
            "label": s.label or s.metric.replace("_", " "),
            "value": None if m is None else m.value,
            "unit": None if m is None else m.unit.value,
            "state": (
                State.UNKNOWN if m is None else _threshold_state(m.value, s.thresholds)
            ).value,
        }

    def _grouped(self, w: ListWidget) -> list[list[Resource]]:
        """Every selected row (unlimited), grouped by ``display.group``, in sort order: a
        group sits where its first member does. Rows without a value stay alone."""
        key = w.display.group or ""
        ordered, _ = self._select(w.source.model_copy(update={"limit": None}))
        groups: dict[str, list[Resource]] = {}
        out: list[list[Resource]] = []
        for r in ordered:
            value = self.field(r, key).value
            if value in (None, ""):
                out.append([r])
            elif str(value) in groups:
                groups[str(value)].append(r)
            else:
                groups[str(value)] = [r]
                out.append(groups[str(value)])
        return out

    def _select(self, source: ListSource) -> tuple[list[Resource], int]:
        """Filter, sort and limit; also returns the count before the limit."""
        items = self.cache.resources(_filter(source))
        for key in reversed(source.sort):  # stable sorts applied last-key-first
            items = self._sorted(items, key)
        total = len(items)
        if source.limit is not None:
            items = items[: source.limit]
        return items, total

    def _sorted(self, items: list[Resource], key: str) -> list[Resource]:
        """Missing values sort last in either direction."""
        field = key.lstrip("-")
        keyed = [(self._sort_value(r, field), r) for r in items]
        present = [(v, r) for v, r in keyed if v is not None]
        present.sort(key=lambda vr: vr[0], reverse=key.startswith("-"))
        return [r for _, r in present] + [r for v, r in keyed if v is None]

    def _sort_value(self, r: Resource, key: str) -> Any:  # noqa: ANN401
        if key.startswith("metric."):
            m = self.cache.metric(r.uid, key.removeprefix("metric."))
            return None if m is None else (0, m.value)
        return _sort_value(r, key)

    def expand(self, w: Widget) -> Widget:
        """The tile as its detail view shows it: every row, and a day of trend."""
        if isinstance(w, ListWidget | BarsWidget | UptimeWidget):
            return w.model_copy(update={"source": w.source.model_copy(update={"limit": None})})
        if isinstance(w, MetricWidget):
            display = w.display.model_copy(update={"sparkline": Sparkline(range="24h")})
            return w.model_copy(update={"display": display})
        return w

    def _chart_series(self, w: ChartWidget) -> list[tuple[str, str, str | None]]:
        """(uid, metric, label) per series: as named, or the busiest ``limit`` resources a
        selection matches, by their latest value of ``metric``."""
        if w.source.series:
            return [(s.resource, s.metric, s.label) for s in w.source.series]
        assert w.source.select is not None and w.source.metric is not None
        metric = w.source.metric
        found = self.cache.resources(_filter(ListSource(select=w.source.select)))
        latest = [(r, self.cache.metric(r.uid, metric)) for r in found]
        ranked = sorted(((r, m) for r, m in latest if m is not None), key=lambda rm: -rm[1].value)
        return [(r.uid, metric, None) for r, _ in ranked[: w.source.limit]]

    async def _chart(
        self, w: ChartWidget, now: datetime, range_: str | None = None
    ) -> ResolvedWidget:
        """Every series averaged into the same buckets over the window, so they share one
        time axis; a bucket with no sample is a gap (None), never a zero."""
        rng = range_ or w.source.range
        to = int(now.timestamp())
        frm = to - parse_duration(rng)
        wanted = self._chart_series(w)
        series: list[dict[str, Any]] = []
        for uid, metric, label in wanted:
            r = self.cache.resource(uid)
            m = self.cache.metric(uid, metric)
            points = (
                await asyncio.to_thread(read_points, self.db, uid, metric, frm, to)
                if self.db is not None
                else []
            )
            values = bucketed(points, frm, to)
            seen = [v for v in values if v is not None]
            series.append(
                {
                    "uid": uid,
                    "metric": metric,
                    "label": label or (r.name if r is not None else uid),
                    "unit": m.unit.value if m is not None else None,
                    "last": m.value if m is not None else None,
                    "min": min(seen) if seen else None,
                    "max": max(seen) if seen else None,
                    "mean": sum(seen) / len(seen) if seen else None,
                    "stale": r.stale if r is not None else True,
                    "values": values,
                }
            )
        lasts = [s["last"] for s in series if s["last"] is not None]
        if not series:
            state, error = State.UNKNOWN, "no series: nothing matches this selection yet"
        elif not lasts:
            state, error = State.UNKNOWN, "no readings yet for any series"
        else:
            state = _threshold_state(max(lasts), w.display.thresholds)
            error = None
        return _tile(
            w,
            state,
            stale=any(s["stale"] for s in series),
            error=error,
            data={
                "range": rng,
                "ranges": sorted({*CHART_RANGES, w.source.range}, key=parse_duration),
                "kind": w.display.kind,
                "stacked": w.display.stacked,
                "timezone": self._timezone() if self._timezone else "UTC",
                "x": bucket_axis(frm, to),
                "series": series,
                "unit": next((s["unit"] for s in series if s["unit"]), None),
                "thresholds": [t.model_dump() for t in w.display.thresholds],
                "format": {"unit": w.display.unit, "precision": w.display.precision},
            },
        )

    async def _heatmap(self, w: HeatmapWidget, now: datetime) -> ResolvedWidget:
        """One metric by weekday and hour, in settings.timezone."""
        uid, metric = w.source.resource, w.source.metric
        r = self.cache.resource(uid)
        m = self.cache.metric(uid, metric)
        to = int(now.timestamp())
        frm = to - parse_duration(w.display.range)
        points = (
            await asyncio.to_thread(read_points, self.db, uid, metric, frm, to)
            if self.db is not None
            else []
        )
        grid = heat(points, self.tz, w.display.agg)
        seen = [v for row in grid for v in row if v is not None]
        error = None
        if r is None:
            error = f"no resource {uid!r} (is its provider polling?)"
        elif not seen:
            error = f"no history yet for {metric!r}"
        return _tile(
            w,
            State.UNKNOWN if r is None else State.UP,
            stale=r.stale if r is not None else False,
            error=error,
            data={
                "grid": grid,
                "min": min(seen) if seen else None,
                "max": max(seen) if seen else None,
                "unit": m.unit.value if m is not None else None,
                "range": w.display.range,
                "agg": w.display.agg,
                "samples": len(points),
                "resource_name": r.name if r is not None else uid,
                "format": {"unit": w.display.unit, "precision": w.display.precision},
            },
        )

    async def _list_with_uptime(self, w: ListWidget, now: datetime) -> ResolvedWidget:
        resolved = self._list(w)
        sparked = [s for s in w.display.stats if s.sparkline]
        if self.db is None or not (w.display.uptime or w.display.trend or sparked):
            return resolved
        to = int(now.timestamp())
        rows = resolved.data["items"]
        for stat, out in zip(w.display.stats, resolved.data["stats"], strict=True):
            if stat.sparkline:
                span = parse_duration(stat.sparkline)
                points = await asyncio.to_thread(
                    read_samples, self.db, stat.resource, stat.metric, to - span, to
                )
                out["sparkline"] = points[:: max(1, len(points) // 60)]
        if w.display.uptime:
            frm = to - RANGES["24h"]
            uids = [r["uid"] for r in rows]
            spans = await asyncio.to_thread(read_spans, self.db, uids, frm, to, to)
            for row in rows:
                s = spans.get(row["uid"], [])
                row["uptime"] = {"cells": cells(s, frm, to, 24), "sla": ratio(tally(s, frm, to))}
        if w.display.trend:
            for row in rows:
                points = await asyncio.to_thread(
                    read_samples, self.db, row["uid"], w.display.trend, to - 6 * 3600, to
                )
                step = max(1, len(points) // 40)
                row["trend"] = points[::step]
        return resolved

    def _status(self, w: StatusWidget) -> ResolvedWidget:
        """The headline over a selection, worst news first."""
        items, total = self._select(w.source.model_copy(update={"limit": None}))
        counts: dict[str, int] = {}
        for r in items:
            state = effective_state(r)[0]
            counts[state] = counts.get(state, 0) + 1
        down, degraded = counts.get("down", 0), counts.get("degraded", 0)
        if not items:
            headline = "Nothing to report"
        elif down:
            headline = "Major outage" if down * 2 >= len(items) else "Partial outage"
        elif degraded:
            headline = "Degraded performance"
        elif counts.get("unknown"):
            headline = "Some systems not reporting"
        else:
            headline = w.display.ok_text
        problems = sorted(
            (r for r in items if effective_state(r)[0] in ("down", "degraded", "unknown")),
            key=lambda r: (-STATE_SEVERITY[State(effective_state(r)[0])], r.name.lower()),
        )[: w.display.show]
        return _tile(
            w,
            _worst(items) if items else State.UNKNOWN,
            stale=any(r.stale for r in items),
            data={
                "headline": headline,
                "counts": counts,
                "total": total,
                "problems": [
                    {
                        "uid": r.uid,
                        "title": r.name,
                        "state": effective_state(r)[0],
                        "links": r.links,
                    }
                    for r in problems
                ],
            },
        )

    async def _incidents(self, w: IncidentsWidget, now: datetime) -> ResolvedWidget:
        """Down (and degraded) spans, newest first, from availability history."""
        if self.db is None:
            return _tile(w, State.UNKNOWN, error="incidents need the metrics store", data={})
        items, _ = self._select(w.source.model_copy(update={"limit": None}))
        by_uid = {r.uid: r for r in items}
        to = int(now.timestamp())
        frm = to - RANGES[w.display.range]
        spans = await asyncio.to_thread(read_spans, self.db, list(by_uid), frm, to, to)
        wanted = {"down", "degraded"} if w.display.include_degraded else {"down"}
        found = [(uid, s) for uid, ss in spans.items() for s in ss if s.state in wanted]
        found.sort(key=lambda us: us[1].start, reverse=True)
        incidents = [
            {
                "uid": uid,
                "title": self._row_title(by_uid[uid], w.display.title),
                "state": s.state,
                "start": s.start,
                "end": None if s.open else s.end,
                "seconds": s.end - s.start,
                "approximate": s.approximate,
                "links": by_uid[uid].links,
            }
            for uid, s in found[: w.display.limit]
        ]
        ongoing = any(i["end"] is None and i["state"] == "down" for i in incidents)
        return _tile(
            w,
            State.DOWN if ongoing else State.UP,
            data={
                "range": w.display.range,
                "incidents": incidents,
                "total": len(found),
                "empty_text": w.display.empty_text,
            },
        )

    def _list(self, w: ListWidget) -> ResolvedWidget:
        if w.display.group:
            groups = self._grouped(w)
            total = len(groups)
            if w.source.limit is not None:
                groups = groups[: w.source.limit]
            items = [g[0] for g in groups]
            counts = {g[0].uid: (len(g), self.field(g[0], w.display.group).value) for g in groups}
        else:
            items, total = self._select(w.source)
            counts = {}
        rows = [
            {
                "uid": r.uid,
                "name": r.name,
                "title": (
                    str(counts[r.uid][1])
                    if r.uid in counts and counts[r.uid][1] not in (None, "")
                    else self._row_title(r, w.display.title)
                ),
                "group_count": counts[r.uid][0] if r.uid in counts else 1,
                "state": r.state.value,
                "stale": r.stale,
                "links": r.links,
                "fields": [self.field(r, key).model_dump() for key in w.display.fields],
                "bar": self._bar(r, w.display.bar),
                "icon": self._icon(r, w.display.icon),
                "image": w.display.image and bool(r.attrs.get("image")),
                "backdrop": w.display.image and bool(r.attrs.get("backdrop")),
                "when": (
                    when(self.field(r, w.display.date).value, self.tz) if w.display.date else None
                ),
            }
            for r in items
        ]
        return _tile(
            w,
            _worst(items),
            stale=any(r.stale for r in items),
            data={
                "items": rows,
                "total": total,
                "empty_text": w.display.empty_text,
                "layout": w.display.layout,
                "today": datetime.now(self.tz).date().isoformat(),
                "stats": [self.stat(s) for s in w.display.stats],
                "dense": w.display.dense,
            },
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
        total: dict[str, Any] | None = None
        pct: float | None = None
        if w.display.total is not None:
            ref = w.display.total
            t = self.cache.metric(ref.resource or uid, ref.metric)
            if t is not None and t.value > 0:
                pct = m.value / t.value * 100
                total = {"value": t.value, "unit": t.unit.value, "pct": pct}
        # With a total, thresholds judge the share of it (memory at 90 %), not raw bytes.
        state = _threshold_state(m.value if pct is None else pct, w.display.thresholds)
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
                "total": total,
                "style": w.display.style,
                "delta": await self._delta(w, m.value, now),
            },
        )

    async def _delta(self, w: MetricWidget, value: float, now: datetime) -> dict[str, Any] | None:
        """The change against ``display.delta`` ago, from the stored sample nearest that time
        (within 5 minutes); None when there is no such sample rather than a guess."""
        if w.display.delta is None or self.db is None:
            return None
        target = int(now.timestamp()) - parse_duration(w.display.delta)
        near = await asyncio.to_thread(
            read_samples, self.db, w.source.resource, w.source.metric, target - 300, target + 300
        )
        if not near:
            return None
        _, then = min(near, key=lambda p: abs(p[0] - target))
        change = value - then
        return {
            "window": w.display.delta,
            "then": then,
            "change": change,
            "pct": change / abs(then) * 100 if then else None,
        }

    def _bars(self, w: BarsWidget) -> ResolvedWidget:
        items, total = self._select(w.source)
        name, unit = w.source.metric, None
        bars: list[dict[str, Any]] = []
        values: list[float] = []
        for r in items:
            m = self.cache.metric(r.uid, name)
            if m is not None:
                unit = m.unit
                values.append(m.value)
            bars.append(
                {
                    "uid": r.uid,
                    "title": self._row_title(r, w.display.title),
                    "value": None if m is None else m.value,
                    # A bar is coloured by its own value; a down resource stays down.
                    "state": (
                        r.state.value
                        if r.state is not State.UP or m is None
                        else _threshold_state(m.value, w.display.thresholds).value
                    ),
                    "stale": r.stale,
                    "links": r.links,
                }
            )
        summary = None
        if w.display.summary and values:
            v = statistics.fmean(values) if w.display.summary == "mean" else max(values)
            summary = {
                "kind": w.display.summary,
                "value": v,
                "state": _threshold_state(v, w.display.thresholds).value,
            }
        scale = w.display.max
        if scale is None:
            peak = max(values, default=0.0)
            scale = max(100.0, peak) if unit is Unit.PCT else (peak or 1.0)
        return _tile(
            w,
            _worst(items),
            stale=any(r.stale for r in items),
            data={
                "layout": w.display.layout,
                "unit": None if unit is None else unit.value,
                "max": scale,
                "format": w.display.format.model_dump(),
                "summary": summary,
                "bars": bars,
                "total": total,
                "empty_text": w.display.empty_text,
            },
        )

    async def _uptime(self, w: UptimeWidget, now: datetime) -> ResolvedWidget:
        d = w.display
        if w.source.resource:
            r = self.cache.resource(w.source.resource)
            uids, items, total = [w.source.resource], ([r] if r else []), 1
        else:
            items, total = self._select(w.source)
            uids = [r.uid for r in items]
        if self.db is None:
            return _tile(w, State.UNKNOWN, error="uptime needs the metrics store", data={})
        to = int(now.timestamp())
        bars_from = to - RANGES[d.range]
        if d.style == "calendar":  # whole local days: start at the first day's midnight
            first = day_cells([], RANGES[d.range] // 86_400, self.tz, now)[0]["t"]
            bars_from = min(bars_from, int(str(first)))
        sla_from = to - RANGES[d.sla_range or d.range]
        spans = await asyncio.to_thread(read_spans, self.db, uids, min(bars_from, sla_from), to, to)
        by_uid = {r.uid: r for r in items}
        rows = []
        for uid in uids:
            s = spans.get(uid, [])
            r = by_uid.get(uid)
            sla_tally = tally(s, sla_from, to)
            rows.append(
                {
                    "uid": uid,
                    "title": self._row_title(r, d.title) if r else uid.rsplit(":", 1)[-1],
                    "state": effective_state(r)[0] if r else State.UNKNOWN.value,
                    "links": r.links if r else {},
                    "cells": (
                        day_cells(s, RANGES[d.range] // 86_400, self.tz, now)
                        if d.style == "calendar"
                        else cells(s, bars_from, to, d.buckets)
                    ),
                    "sla": ratio(sla_tally, exclude_unknown=d.exclude_unknown),
                    "down_seconds": sla_tally["down"],
                    "unobserved_seconds": sla_tally["unknown"] + sla_tally["not_observed"],
                    "approximate": any(x.approximate for x in s),
                }
            )
        return _tile(
            w,
            _worst(items) if items else State.UNKNOWN,
            stale=any(r.stale for r in items),
            data={
                "range": d.range,
                "style": d.style,
                "sla_range": d.sla_range or d.range,
                "show_sla": d.show_sla,
                "bucket_seconds": RANGES[d.range] // d.buckets,
                "rows": rows,
                "total": total,
                "empty_text": d.empty_text,
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

    def _bar(self, r: Resource, key: str | None) -> float | None:
        if key is None:
            return None
        v = self.field(r, key).value
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    def _icon(self, r: Resource, key: str | None) -> str | None:
        """The icon name when it is one /icons will serve; else None (a letter badge). A
        display name is tried as its dashboard-icons guess ("Home Assistant" →
        home-assistant), as the apps list does, so `icon: name` works for a web check."""
        v = self.field(r, key).value if key else None
        if not isinstance(v, str) or not v.strip():
            return None
        name = v.strip().lower()
        if resolve_icon(name):
            return name
        if "/" in v or "." in v:  # a path or a file name is never guessed at
            return None
        guess = icon_slug(v)
        return guess if guess and resolve_icon(guess) else None

    def _row_title(self, r: Resource, key: str | None) -> str:
        value = self.field(r, key).value if key else None
        return str(value) if value not in (None, "") else r.name

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


def when(value: Any, tz: tzinfo) -> dict[str, Any] | None:  # noqa: ANN401
    """A row's place on the calendar: its day (and time) in ``tz``. A date alone, or midnight
    UTC — how *arr APIs write a release day — is that day with no time, never shifted."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        at = datetime.fromtimestamp(value, UTC)
    elif isinstance(value, str) and value:
        text = value.strip()
        if len(text) == 10:
            try:
                return {"day": date.fromisoformat(text).isoformat(), "time": None}
            except ValueError:
                return None
        try:
            at = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
    else:
        return None
    utc = at.astimezone(UTC)
    if (utc.hour, utc.minute, utc.second) == (0, 0, 0):
        return {"day": utc.date().isoformat(), "time": None}
    local = at.astimezone(tz)
    return {"day": local.date().isoformat(), "time": local.strftime("%H:%M")}


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


def _filter(source: ListSource) -> ResourceFilter:
    sel = source.select
    return ResourceFilter(
        provider=_as_list(sel.provider),
        kind=_as_list(sel.kind),
        state=_as_list(sel.state),
        labels=sel.label,
        attrs=sel.attrs,
        missing=sel.missing,
    )


def _worst(items: Sequence[Resource]) -> State:
    worst = max((STATE_SEVERITY[r.state] for r in items), default=0)
    return next(s for s, sev in STATE_SEVERITY.items() if sev == worst) if items else State.UP


def _threshold_state(value: float, thresholds: list[Threshold]) -> State:
    """The last threshold crossed wins; none crossed is up."""
    state = State.UP
    for t in thresholds:
        above = t.gte is not None and value >= t.gte
        below = t.lte is not None and value <= t.lte
        if above or below:
            state = State.DOWN if t.state == "error" else State.DEGRADED
    return state


def _single_resource(w: Widget) -> str | None:
    """The one resource a tile is about, when it is about exactly one."""
    if isinstance(w, ResourceWidget | MetricWidget):
        return w.source.resource
    if isinstance(w, UptimeWidget | HeatmapWidget):
        return w.source.resource
    return None


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


def _natural(text: str) -> tuple[str | int, ...]:
    """cpu2 before cpu10. Digits land at odd indexes, so ints only meet ints."""
    return tuple(int(p) if i % 2 else p for i, p in enumerate(re.split(r"(\d+)", text.lower())))


def _sort_value(r: Resource, key: str) -> Any:  # noqa: ANN401
    """A comparable value, or None for "missing" (the caller puts those last). Numbers
    and text never meet: numbers are keyed (0, n) and text (1, ...)."""
    if key.startswith("attrs."):
        return _scalar_sort_value(_dig(r.attrs, key.removeprefix("attrs.")))
    if key == "state_severity":
        return (0, STATE_SEVERITY[r.state])
    if key == "state":
        return (1, (r.state.value,))
    if key in ("name", "kind", "provider"):
        return (1, _natural(str(getattr(r, key))))
    return None


def _scalar_sort_value(v: Any) -> Any:  # noqa: ANN401
    if v is None:
        return None
    if isinstance(v, int | float) and not isinstance(v, bool):
        return (0, v)
    return (1, _natural(str(v)))


__all__ = ["BoardSummary", "FieldValue", "ResolvedBoard", "ResolvedWidget", "WidgetEngine"]
