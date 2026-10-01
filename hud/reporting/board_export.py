# SPDX-License-Identifier: Apache-2.0
"""Board export (PLAN.md §9.5): a board read as report sections — exported now as PDF or
CSV over 24 hours, 7 days or 30 days, or saved as a weekly report file.

The mapping is fixed and stated, so an export never guesses:

- a chart of named series → a trend chart of those series; a chart of a selection, and
  bars → the busiest of that selection over the window (top_n);
- single readings — metric tiles, heatmaps, a card's hero stats, a section's summary
  stats → one trend chart per board section, one per unit (8 lines at most each);
- uptime and status → an availability table; incidents → the same, worst first;
- capacity → capacity with its projection;
- a list showing uptime → its availability table; showing a trend → its top_n;
- other lists, links, embeds and plain cards hold no history: they are named as left out.

The same sections serve both paths: :func:`export` renders them through the report
pipeline, and :func:`report_text` writes them as an ordinary ``Report`` file that is the
user's to edit — HUD never rewrites it.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any, Literal

from pydantic import TypeAdapter
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from sqlalchemy import Engine

from hud import __version__
from hud.collector import LiveCache
from hud.config.schemas.board import (
    BarsWidget,
    BoardDocument,
    CapacityWidget,
    ChartWidget,
    HeatmapWidget,
    HeroStat,
    IncidentsWidget,
    ListWidget,
    MetricWidget,
    ResourceWidget,
    Select,
    StatusWidget,
    UptimeWidget,
    Widget,
)
from hud.config.schemas.report import Section
from hud.config.writer import IndentStyle, dump_yaml
from hud.reporting import render
from hud.reporting.sections import Window, build
from hud.widgets.builder import to_node

RANGES: dict[str, int] = {"24h": 86_400, "7d": 7 * 86_400, "30d": 30 * 86_400}
Range = Literal["24h", "7d", "30d"]
Format = Literal["pdf", "csv"]
MAX_SECTIONS = 20  # what a Report holds
MAX_SERIES = 8  # lines in one trend chart
UNIT_WORDS = {
    "pct": "percent",
    "bytes": "bytes",
    "bps": "throughput",
    "count": "counts",
    "seconds": "durations",
    "celsius": "temperatures",
    "watts": "power",
    "none": "readings",
}
_WORSE_THAN_99 = {"uptime_pct": {"lt": 99.0, "style": "warn"}}
_SECTIONS: TypeAdapter[list[Section]] = TypeAdapter(list[Section])


@dataclass
class Plan:
    """The board as report sections (mappings as a report file holds them), and what had
    nothing to report."""

    title: str
    sections: list[dict[str, Any]] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)


def plan(board: BoardDocument, cache: LiveCache) -> Plan:
    """Sections in the board's own order: loose tiles first, then each titled section."""
    heading = board.metadata.title or board.metadata.name
    out = Plan(heading)
    known = {s.id for s in board.spec.sections}
    bands: list[tuple[str | None, str, list[HeroStat]]] = [(None, heading, [])]
    bands += [(s.id, s.title, list(s.stats)) for s in board.spec.sections]
    seen: set[str] = set()
    for band, title, stats in bands:
        widgets = sorted(
            (w for w in board.spec.widgets if (w.section if w.section in known else None) == band),
            key=lambda w: (w.grid.row, w.grid.col),
        )
        readings = [_series(s.resource, s.metric, s.label) for s in stats]
        mapped: list[dict[str, Any]] = []
        for w in widgets:
            section = _section(w)
            own = _readings(w)
            readings += own
            sections = ([section] if section is not None else []) + _from_list(w)
            mapped += sections
            if not sections and not own:
                out.left_out.append(_name(w))
        for section in [*_trends(title, readings, cache), *mapped]:
            key = json.dumps({k: v for k, v in section.items() if k != "title"}, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.sections.append(section)
    if len(out.sections) > MAX_SECTIONS:
        extra = len(out.sections) - MAX_SECTIONS
        out.sections = out.sections[:MAX_SECTIONS]
        out.left_out.append(f"{extra} more section(s): a report holds {MAX_SECTIONS}")
    return out


def _from_list(w: Widget) -> list[dict[str, Any]]:
    """A list's rows carry history only where it shows it: an uptime strip, a trend."""
    if not isinstance(w, ListWidget):
        return []
    title = w.title or "List"
    out = []
    if w.display.uptime:
        out.append(_uptime(title, w.source.select))
    if w.display.trend:
        trend = w.display.trend
        out.append(_top(f"{title} · {trend}", trend, w.source.select, w.source.limit or 10))
    return out


def _readings(w: Widget) -> list[dict[str, Any]]:
    """Single readings, which go into their band's trend chart rather than a section."""
    if isinstance(w, MetricWidget | HeatmapWidget):
        return [_series(w.source.resource, w.source.metric, w.title)]
    if isinstance(w, ResourceWidget):
        return [_series(s.resource, s.metric, s.label) for s in w.display.stats]
    return []


def _section(w: Widget) -> dict[str, Any] | None:
    """The report section for one widget, or None when it has none of its own."""
    section: dict[str, Any] | None = None
    match w:
        case ChartWidget() if w.source.series:
            series = [_series(s.resource, s.metric, s.label) for s in w.source.series]
            section = {"kind": "trend_chart", "title": w.title or "Trend", "series": series}
        case ChartWidget() if w.source.select is not None and w.source.metric is not None:
            section = _top(w.title, w.source.metric, w.source.select, w.source.limit)
        case BarsWidget():
            section = _top(w.title, w.source.metric, w.source.select, w.source.limit or 10)
        case UptimeWidget() if w.source.resource is not None:
            section = {"kind": "uptime_table", "title": w.title or "Availability"}
            section |= {"resource": w.source.resource, "highlight": _WORSE_THAN_99}
        case UptimeWidget():
            section = _uptime(w.title or "Availability", w.source.select)
        case StatusWidget():
            section = _uptime(w.title or "Status", w.source.select)
        case IncidentsWidget():
            section = {
                "kind": "uptime_table",
                "title": w.title or "Incidents",
                "select": _select(w.source.select),
                "columns": ["name", "incident_count", "downtime_total", "longest_outage"],
                "sort": "-incident_count",
            }
        case CapacityWidget():
            section = {
                "kind": "capacity",
                "title": w.title or "Disks",
                "select": _select(w.source.select),
                "metric": w.display.used,
            }
    return section


def _series(resource: str, metric: str, label: str | None) -> dict[str, Any]:
    out = {"metric": metric, "resource": resource}
    if label:
        out["label"] = label
    return out


def _select(sel: Select) -> dict[str, Any]:
    return sel.model_dump(mode="json", exclude_none=True)


def _top(title: str | None, metric: str, sel: Select, limit: int) -> dict[str, Any]:
    return {
        "kind": "top_n",
        "title": title or f"Top {metric}",
        "metric": metric,
        "select": _select(sel),
        "limit": min(limit, 100),
    }


def _uptime(title: str, sel: Select) -> dict[str, Any]:
    return {
        "kind": "uptime_table",
        "title": title,
        "select": _select(sel),
        "highlight": _WORSE_THAN_99,
    }


def _trends(title: str, readings: list[dict[str, Any]], cache: LiveCache) -> list[dict[str, Any]]:
    """One trend chart per unit (lines of one unit share an axis), at most 8 lines each."""
    by_unit: dict[str | None, list[dict[str, Any]]] = {}
    keys: set[tuple[str, str]] = set()
    for r in readings:
        key = (r["resource"], r["metric"])
        if key in keys:
            continue
        keys.add(key)
        live = cache.metric(r["resource"], r["metric"])
        unit = live.unit.value if live is not None and live.unit is not None else None
        by_unit.setdefault(unit, []).append(r)
    out = []
    for unit, series in by_unit.items():
        name = (
            title
            if len(by_unit) == 1
            else f"{title} · {UNIT_WORDS.get(unit or '', unit or 'other')}"
        )
        for i in range(0, len(series), MAX_SERIES):
            part = f" ({i // MAX_SERIES + 1})" if len(series) > MAX_SERIES else ""
            out.append(
                {"kind": "trend_chart", "title": name + part, "series": series[i : i + MAX_SERIES]}
            )
    return out


def _name(w: Widget) -> str:
    return f"{w.title or w.id} ({w.type})"


# ------------------------------------------------------------------------ export now


def export(
    engine: Engine,
    cache: LiveCache,
    found: Plan,
    window: Window,
    fmt: Format,
) -> bytes:
    """The board over ``window`` through the report pipeline. A pdf that cannot render
    raises: the caller says so rather than handing back something else."""
    built = [build(s, engine, cache, window) for s in _SECTIONS.validate_python(found.sections)]
    if fmt == "csv":
        return render.csv_text(built, window.tz).encode()
    meta = render.ReportMeta(
        title=found.title,
        since=window.since,
        until=window.until,
        tz=window.tz,
        version=__version__,
        left_out=found.left_out,
    )
    return render.pdf(render.html(meta, built))


def window_for(range_: Range, now: float, tz: tzinfo) -> Window:
    until = int(now)
    return Window(until - RANGES[range_], until, tz)


# ------------------------------------------------------------------------ save as report


def report_text(board: BoardDocument, found: Plan, today: datetime) -> str:
    """A weekly ``Report`` with the board's sections: Mondays 07:00 in settings.timezone,
    over the past 7 days, as html, csv and pdf."""
    name = board.metadata.name
    title = board.metadata.title or name
    spec = CommentedMap()
    spec["schedule"] = "0 7 * * MON"
    spec["window"] = to_node({"from": "-7d", "to": "now"})
    spec["sections"] = CommentedSeq(to_node(s) for s in found.sections)
    spec["outputs"] = CommentedSeq(to_node({"format": f}) for f in ("html", "csv", "pdf"))
    doc = CommentedMap()
    doc["apiVersion"] = "hud/v1"
    doc["kind"] = "Report"
    doc["metadata"] = CommentedMap(name=name, title=f"{title} weekly")
    doc["spec"] = spec
    text = (
        f"Saved from the {title} board on {today.date().isoformat()}; HUD never rewrites this "
        "file, so edit it freely. Runs Mondays at 07:00 (settings.timezone) over the past week."
    )
    if found.left_out:
        text += " Not included, no history to report: " + ", ".join(found.left_out) + "."
    doc.yaml_set_start_comment("\n".join(textwrap.wrap(text, 86)))
    # As the bundled files are written: list items indented under their key.
    return dump_yaml(doc, indent=IndentStyle(mapping=2, sequence=4, offset=2))


__all__ = ["RANGES", "Format", "Plan", "Range", "export", "plan", "report_text", "window_for"]
