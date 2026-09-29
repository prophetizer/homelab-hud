# SPDX-License-Identifier: Apache-2.0
"""Report renderers: a self-contained, printable HTML page and a long-format CSV.

The HTML has no scripts and no outside resources — inline styles, charts drawn as SVG on
the server — so it opens anywhere, prints cleanly (the PDF output will render this same
page), and is served with a sandboxing CSP. Every string from a provider is escaped by
Jinja's autoescape. Colour is for status only (invariant 9): series are shades of ink,
only a highlighted cell is amber or red.

The CSV is one row per value — section, title, row, key, value — so any spreadsheet can
pivot it, whatever mix of sections the report has.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from importlib import resources
from typing import Any

from jinja2 import Environment

from hud.reporting.sections import SectionResult

_BIN = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_BITS = ("bit/s", "kbit/s", "Mbit/s", "Gbit/s", "Tbit/s")


def fmt(value: object, unit: str | None = None, precision: int = 1) -> str:
    """A value for people: 99.94 %, 3.2 GiB, 12 Mbit/s, 1h 12m, 184 ms — or an em dash."""
    if value is None:
        return "—"
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value)
    v = float(value)
    by_unit = {
        "pct": lambda: f"{v:.2f} %" if v >= 99.9 else f"{v:.{precision}f} %",
        "bytes": lambda: _scaled(v, 1024, _BIN, precision),
        "bps": lambda: _scaled(v, 1000, _BITS, precision),
        "seconds": lambda: duration(v),
        "celsius": lambda: f"{v:.{precision}f} °C",
    }
    if unit in by_unit:
        return by_unit[unit]()
    return f"{int(v):,}" if v.is_integer() else f"{v:,.{precision}f}"


def _scaled(v: float, base: int, names: tuple[str, ...], precision: int) -> str:
    i = 0
    while abs(v) >= base and i < len(names) - 1:
        v /= base
        i += 1
    return f"{v:.{precision}f} {names[i]}" if i else f"{v:.0f} {names[0]}"


def duration(seconds: float) -> str:
    if 0 < abs(seconds) < 1:
        return f"{seconds * 1000:.0f} ms"
    s = int(seconds)
    d, h, m = s // 86_400, (s % 86_400) // 3600, (s % 3600) // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s % 60}s"
    return f"{s}s"


_COLUMN_LABELS = {
    "name": "Name",
    "uptime_pct": "Uptime",
    "downtime_total": "Down for",
    "incident_count": "Outages",
    "longest_outage": "Longest",
    "used_pct": "Used",
    "pct_per_day": "Growth / day",
    "full_on": "Full on",
    "reaches_warn_on": "Reaches warning",
    "verdict": "Trend",
    "value": "Value",
    "when": "When",
    "severity": "Severity",
    "message": "Event",
}
_UNITS = {
    "uptime_pct": "pct",
    "downtime_total": "seconds",
    "longest_outage": "seconds",
    "used_pct": "pct",
}
_VERDICTS = {
    "filling": "filling",
    "steady": "steady",
    "shrinking": "shrinking",
    "unclear": "no clear trend",
    "too_little": "not enough history",
}


def cell(row: dict[str, Any], col: str) -> str:
    v = row.get(col)
    if col == "pct_per_day":
        return "—" if v is None else f"{v:+.2f} %"
    if col == "verdict":
        text = _VERDICTS.get(str(v), str(v))
        return f"{text} (rough)" if row.get("confidence") == "rough" else text
    if col == "value":
        return fmt(v, row.get("unit"))
    return fmt(v, _UNITS.get(col))


# Neutral inks and dashes for series (invariant 9).
_INKS = [("#1c2127", ""), ("#4e5966", "6 4"), ("#7e8a97", ""), ("#4e5966", "2 3")]


def svg_chart(section: SectionResult, width: int = 720, height: int = 170) -> dict[str, Any]:
    """Polylines for each series on one time axis, scaled to the section's values."""
    pad_l, pad_r, pad_t, pad_b = 64, 8, 8, 22
    points = [p for s in section.series for p in s["points"]]
    if not points:
        return {"lines": [], "width": width, "height": height}
    t0, t1 = min(p[0] for p in points), max(p[0] for p in points)
    lo = min(0.0, *(p[1] for p in points))
    hi = max(p[1] for p in points) or 1.0
    span_t = (t1 - t0) or 1
    span_v = (hi - lo) or 1.0

    def xy(t: int, v: float) -> str:
        x = pad_l + (t - t0) / span_t * (width - pad_l - pad_r)
        y = pad_t + (1 - (v - lo) / span_v) * (height - pad_t - pad_b)
        return f"{x:.1f},{y:.1f}"

    unit = next((s["unit"] for s in section.series if s["unit"]), None)
    lines = [
        {
            "points": " ".join(xy(t, v) for t, v in s["points"]),
            "ink": _INKS[i % len(_INKS)][0],
            "dash": _INKS[i % len(_INKS)][1],
            "label": s["label"],
            "min": fmt(s["min"], s["unit"]),
            "avg": fmt(s["avg"], s["unit"]),
            "max": fmt(s["max"], s["unit"]),
        }
        for i, s in enumerate(section.series)
        if s["points"]
    ]
    return {
        "lines": lines,
        "width": width,
        "height": height,
        "left": pad_l,
        "bottom": height - pad_b,
        "top": pad_t,
        "right": width - pad_r,
        "hi": fmt(hi, unit, 0),
        "lo": fmt(lo, unit, 0),
        "t0": t0,
        "t1": t1,
    }


def cell_style(kind: str, row: dict[str, Any], col: str) -> str:
    """warn / error on a highlighted cell (a report's own rule), or a disk due to fill."""
    style = (row.get("highlight") or {}).get(col)
    if style:
        return str(style)
    return "warn" if kind == "capacity" and col == "name" and row.get("warn") else ""


_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_ENV.globals.update(cell=cell, cell_style=cell_style)
_PAGE = _ENV.from_string(
    (resources.files("hud.reporting") / "report.html.j2").read_text(encoding="utf-8")
)
_TEXT_COLUMNS = {"name", "verdict", "when", "severity", "message", "full_on", "reaches_warn_on"}


@dataclass(frozen=True)
class ReportMeta:
    title: str
    since: int
    until: int
    tz: tzinfo
    version: str
    skipped: list[str] = field(default_factory=list)  # outputs this build cannot produce yet


def html(meta: ReportMeta, sections: list[SectionResult]) -> str:
    def local(ts: int, pattern: str) -> str:
        return datetime.fromtimestamp(ts, meta.tz).strftime(pattern)

    views: list[dict[str, Any]] = []
    for s in sections:
        view: dict[str, Any] = {
            "kind": s.kind,
            "title": s.title,
            "note": s.note,
            "columns": s.columns,
            "rows": s.rows,
        }
        if s.kind == "trend_chart":
            chart = svg_chart(s)
            view["chart"] = chart
            if chart["lines"]:
                view["start_label"] = local(chart["t0"], "%a %d %b %H:%M")
                view["end_label"] = local(chart["t1"], "%a %d %b %H:%M")
        views.append(view)
    stamp = "%a %d %b %Y %H:%M"
    return _PAGE.render(
        title=meta.title,
        period=f"{local(meta.since, stamp)} to {local(meta.until, stamp)}",
        sections=views,
        labels=_COLUMN_LABELS,
        text_columns=_TEXT_COLUMNS,
        version=meta.version,
        generated=local(meta.until, stamp),
        tz=str(meta.tz),
        skipped=", ".join(meta.skipped),
    )


def csv_text(sections: list[SectionResult], tz: tzinfo) -> str:
    """section, title, row, key, value — one line per value, raw numbers (not formatted)."""
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["section", "title", "row", "key", "value"])
    for s in sections:
        if s.kind == "trend_chart":
            for series in s.series:
                for ts, v in series["points"]:
                    stamp = datetime.fromtimestamp(ts, tz).isoformat()
                    out.writerow([s.kind, s.title, stamp, series["label"], v])
            continue
        for i, row in enumerate(s.rows, start=1):
            label = row.get("name") or row.get("when") or str(i)
            for key in [c for c in s.columns if c != "name"] or list(row):
                value = row.get(key)
                out.writerow([s.kind, s.title, label, key, "" if value is None else value])
        if s.note and not s.rows and not s.series:
            out.writerow([s.kind, s.title, "", "note", s.note])
    return buf.getvalue()


__all__ = ["ReportMeta", "csv_text", "duration", "fmt", "html", "svg_chart"]
