# SPDX-License-Identifier: Apache-2.0
"""Report sections (PLAN.md §9.3): each turns HUD's history over a window into rows.

Plain data in, plain data out — the renderers (HTML, CSV) never query anything. Every
section reads through the same pieces the boards use: availability spans for uptime, the
query planner for series, the fill forecast for capacity, the events table for events.
Nothing is estimated where there is no data: a missing value is None and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import Engine, select

from hud.collector import LiveCache, ResourceFilter
from hud.config.schemas.board import Select
from hud.config.schemas.duration import parse_duration
from hud.config.schemas.report import Capacity, Events, Section, TopN, TrendChart, UptimeTable
from hud.models import Resource
from hud.reporting.query import read_range
from hud.store.tables import events as events_t
from hud.store.tables import series as series_t
from hud.widgets.series import forecast
from hud.widgets.uptime import ratio, read_spans, tally


@dataclass(frozen=True)
class Window:
    since: int
    until: int
    tz: tzinfo

    @property
    def seconds(self) -> int:
        return self.until - self.since


@dataclass
class SectionResult:
    kind: str
    title: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    # trend_chart only: one entry per series, with its points.
    series: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


def _selected(cache: LiveCache, sel: Select) -> list[Resource]:
    def as_list(v: Any) -> list[Any] | None:  # noqa: ANN401
        return v if isinstance(v, list) else ([v] if v is not None else None)

    found = cache.resources(
        ResourceFilter(
            provider=as_list(sel.provider),
            kind=as_list(sel.kind),
            state=as_list(sel.state),
            labels=sel.label,
            attrs=sel.attrs,
            missing=sel.missing,
        )
    )
    return sorted(found, key=lambda r: r.name.lower())


def uptime_table(s: UptimeTable, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    found = _selected(cache, s.select)
    spans = read_spans(db, [r.uid for r in found], w.since, w.until, w.until)
    rows: list[dict[str, Any]] = []
    for r in found:
        mine = spans.get(r.uid, [])
        t = tally(mine, w.since, w.until)
        # Down time inside the window; a zero-length span (opened and closed at once, e.g.
        # around a restart) is not an outage.
        overlaps = (min(x.end, w.until) - max(x.start, w.since) for x in mine if x.state == "down")
        downs = [d for d in overlaps if d > 0]
        rows.append(
            {
                "uid": r.uid,
                "name": r.name,
                "uptime_pct": ratio(t),
                "downtime_total": t["down"],
                "incident_count": len(downs),
                "longest_outage": max(downs, default=0),
                "not_observed": t["not_observed"],
            }
        )
    key = s.sort.lstrip("-")
    rows.sort(
        key=lambda row: (row[key] is None, row[key] if row[key] is not None else 0),
        reverse=s.sort.startswith("-"),
    )
    for row in rows:
        row["highlight"] = {
            col: h.style
            for col, h in s.highlight.items()
            if isinstance(row.get(col), int | float)
            and ((h.lt is not None and row[col] < h.lt) or (h.gt is not None and row[col] > h.gt))
        }
    return SectionResult("uptime_table", s.title, list(s.columns), rows)


def trend_chart(s: TrendChart, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    out: list[dict[str, Any]] = []
    for sr in s.series:
        points = read_range(
            db, (sr.resource, sr.metric), (w.since, w.until), agg=sr.agg, tier=s.tier
        )
        r = cache.resource(sr.resource)
        values = [v for _, v in points]
        out.append(
            {
                "uid": sr.resource,
                "metric": sr.metric,
                "label": sr.label or (f"{r.name} {sr.metric}" if r else sr.metric),
                "unit": _unit(db, sr.resource, sr.metric),
                "points": points,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "avg": sum(values) / len(values) if values else None,
            }
        )
    note = None if any(x["points"] for x in out) else "no history in this window"
    return SectionResult("trend_chart", s.title, series=out, note=note)


def top_n(s: TopN, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    rows: list[dict[str, Any]] = []
    for r in _selected(cache, s.select):
        points = read_range(db, (r.uid, s.metric), (w.since, w.until), agg=s.agg)
        if not points:
            continue
        values = [v for _, v in points]
        value = max(values) if s.agg == "max" else sum(values) / len(values)
        rows.append({"uid": r.uid, "name": r.name, "value": value})
    rows.sort(key=lambda row: row["value"], reverse=True)
    unit = _unit(db, rows[0]["uid"], s.metric) if rows else None
    for row in rows:
        row["unit"] = unit
    note = None if rows else f"no history for {s.metric} in this window"
    return SectionResult("top_n", s.title, ["name", "value"], rows[: s.limit], note=note)


def capacity(s: Capacity, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    horizon_days = parse_duration(s.projection.horizon) / 86_400
    warn = s.projection.warn_at_pct
    rows: list[dict[str, Any]] = []
    for r in _selected(cache, s.select):
        points = read_range(db, (r.uid, s.metric), (w.since, w.until))
        live = cache.metric(r.uid, s.metric)
        current = live.value if live is not None else (points[-1][1] if points else None)
        if current is None:
            continue
        f = forecast(points, current, w.seconds)
        per_day = f.get("pct_per_day")
        row: dict[str, Any] = {
            "uid": r.uid,
            "name": r.name,
            "used_pct": current,
            "verdict": f["verdict"],
            "pct_per_day": per_day,
            "confidence": f.get("confidence"),
            "full_in_days": f.get("days"),
            "full_on": None,
            "reaches_warn_on": None,
            "at_horizon_pct": None,
            "warn": current >= warn,
        }
        if f["verdict"] == "filling" and isinstance(per_day, float) and per_day > 0:
            end = datetime.fromtimestamp(w.until, w.tz)
            days = f.get("days")
            if isinstance(days, float):
                row["full_on"] = (end + timedelta(days=days)).date().isoformat()
            row["at_horizon_pct"] = min(100.0, current + per_day * horizon_days)
            if current < warn:
                to_warn = (warn - current) / per_day
                row["reaches_warn_on"] = (end + timedelta(days=to_warn)).date().isoformat()
                row["warn"] = to_warn <= horizon_days
        rows.append(row)
    rows.sort(key=lambda row: (not row["warn"], row["full_in_days"] or float("inf")))
    columns = ["name", "used_pct", "pct_per_day", "full_on", "reaches_warn_on", "verdict"]
    note = None if rows else "no disks matched"
    return SectionResult("capacity", s.title, columns, rows, note=note)


def events(s: Events, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    with db.connect() as conn:
        found = conn.execute(
            select(
                events_t.c.ts,
                events_t.c.resource_uid,
                events_t.c.severity,
                events_t.c.type,
                events_t.c.message,
            )
            .where(
                events_t.c.ts >= w.since,
                events_t.c.ts <= w.until,
                events_t.c.severity.in_(s.severity),
            )
            .order_by(events_t.c.ts.desc())
            .limit(s.limit)
        ).all()
    rows = []
    for ts, uid, severity, type_, message in found:
        r = cache.resource(uid)
        rows.append(
            {
                "ts": int(ts),
                "when": datetime.fromtimestamp(ts, w.tz).strftime("%a %d %b %H:%M"),
                "uid": uid,
                "name": r.name if r else uid,
                "severity": severity,
                "type": type_,
                "message": message,
            }
        )
    note = None if rows else "nothing at these severities in this window"
    return SectionResult("events", s.title, ["when", "severity", "message"], rows, note=note)


def _unit(db: Engine, uid: str, metric: str) -> str | None:
    with db.connect() as conn:
        unit = conn.execute(
            select(series_t.c.unit).where(
                series_t.c.resource_uid == uid, series_t.c.metric == metric
            )
        ).scalar()
    return str(unit) if unit is not None else None


def build(section: Section, db: Engine, cache: LiveCache, w: Window) -> SectionResult:
    """One section; a failure is that section's note, never the whole report's."""
    try:
        match section:
            case UptimeTable():
                return uptime_table(section, db, cache, w)
            case TrendChart():
                return trend_chart(section, db, cache, w)
            case TopN():
                return top_n(section, db, cache, w)
            case Capacity():
                return capacity(section, db, cache, w)
            case _:
                return events(section, db, cache, w)
    except Exception as exc:  # one section failing must not sink the report
        note = f"failed: {type(exc).__name__}: {exc}"
        return SectionResult(section.kind, section.title, note=note)


__all__ = ["SectionResult", "Window", "build"]
