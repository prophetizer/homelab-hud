# SPDX-License-Identifier: Apache-2.0
"""``/config/reports/*.yaml`` — scheduled reports (PLAN.md §9.2, §9.3).

A report reads HUD's own history over a window and writes files: HTML (printable), CSV and
PDF; a webhook output is accepted as written but sends nothing yet — the run says so rather
than failing silently. Sections (v1 subset): uptime_table, trend_chart,
top_n, capacity, events. diff and raw_table come later.

Added 2026-09-29. `Report` is not part of the frozen dashboard/v1 Provider/Board contract;
a bad report file is quarantined on its own, like a board (PLAN §12).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import Field, field_validator, model_validator

from hud.config.schemas.base import Document, Metadata
from hud.config.schemas.board import Select, _Spec
from hud.config.schemas.duration import parse_duration

MAX_WINDOW = 400 * 86_400
_WINDOW_FROM = re.compile(r"-(\d+[smhd])")
UptimeColumn = Literal["name", "uptime_pct", "downtime_total", "incident_count", "longest_outage"]
UPTIME_COLUMNS: tuple[str, ...] = get_args(UptimeColumn)
Severity = Literal["info", "warn", "error"]


def _all_columns() -> list[UptimeColumn]:
    return ["name", "uptime_pct", "downtime_total", "incident_count", "longest_outage"]


def _warn_and_error() -> list[Severity]:
    return ["warn", "error"]


class ReportMetadata(Metadata):
    name: str

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            msg = "report name must be lowercase [a-z0-9_-] (it names the output files)"
            raise ValueError(msg)
        return v


class Window(_Spec):
    """``from: -7d`` to ``to: now`` — the only form v1 reads."""

    from_: str = Field(default="-7d", alias="from")
    to: Literal["now"] = "now"

    @field_validator("from_")
    @classmethod
    def _relative(cls, v: str) -> str:
        m = _WINDOW_FROM.fullmatch(v.strip())
        if m is None or not 0 < parse_duration(m.group(1)) <= MAX_WINDOW:
            msg = "window.from must be a relative duration up to 400d, e.g. -7d"
            raise ValueError(msg)
        return v.strip()

    @property
    def seconds(self) -> int:
        return parse_duration(self.from_.lstrip("-"))


class Highlight(_Spec):
    lt: float | None = None
    gt: float | None = None
    style: Literal["warn", "error"] = "warn"


class UptimeTable(_Spec):
    kind: Literal["uptime_table"]
    title: str = "Service availability"
    select: Select = Select()
    columns: list[UptimeColumn] = Field(default_factory=_all_columns)
    # A column to sort by, "-" first for descending; uptime ascending puts the worst first.
    sort: str = "uptime_pct"
    highlight: dict[str, Highlight] = Field(default_factory=dict)

    @field_validator("sort")
    @classmethod
    def _sort(cls, v: str) -> str:
        if v.lstrip("-") not in UPTIME_COLUMNS:
            msg = f"sort must be one of {', '.join(UPTIME_COLUMNS)} (optionally with -)"
            raise ValueError(msg)
        return v


class TrendSeries(_Spec):
    metric: str
    resource: str
    label: str | None = None
    agg: Literal["avg", "min", "max", "last"] = "avg"


class TrendChart(_Spec):
    kind: Literal["trend_chart"]
    title: str = "Trend"
    series: list[TrendSeries] = Field(min_length=1, max_length=8)
    tier: Literal["auto", "5m", "1h", "1d"] = "auto"


class TopN(_Spec):
    kind: Literal["top_n"]
    title: str = "Top"
    metric: str
    select: Select = Select()
    agg: Literal["avg", "max"] = "avg"
    limit: int = Field(default=10, ge=1, le=100)


class Projection(_Spec):
    method: Literal["linear"] = "linear"
    horizon: str = "180d"
    warn_at_pct: float = Field(default=85.0, gt=0, le=100)

    @field_validator("horizon")
    @classmethod
    def _dur(cls, v: str) -> str:
        parse_duration(v)
        return v


class Capacity(_Spec):
    kind: Literal["capacity"]
    title: str = "Storage growth and projection"
    # The disks: a selection (Glances filesystems, *arr storage) whose `metric` is the share
    # used, 0-100 (used_pct in the bundled templates).
    select: Select = Select(kind="filesystem")
    metric: str = "used_pct"
    projection: Projection = Projection()


class Events(_Spec):
    kind: Literal["events"]
    title: str = "Notable events"
    severity: list[Severity] = Field(default_factory=_warn_and_error)
    limit: int = Field(default=50, ge=1, le=1000)


Section = Annotated[
    UptimeTable | TrendChart | TopN | Capacity | Events, Field(discriminator="kind")
]


class Output(_Spec):
    format: Literal["html", "csv", "pdf", "webhook"]
    # Under /data/reports; {{ name }} and {{ date }} are filled in. Webhook outputs use url.
    path: str | None = None
    url: str | None = None
    body: Literal["summary"] | None = None

    @model_validator(mode="after")
    def _where(self) -> Self:
        if self.format == "webhook":
            if not self.url:
                msg = "a webhook output needs url"
                raise ValueError(msg)
        elif self.path is not None and (".." in self.path.split("/") or "\\" in self.path):
            msg = "path must stay under /data/reports"
            raise ValueError(msg)
        return self


class ReportSpec(_Spec):
    schedule: str = "0 7 * * MON"
    timezone: str | None = None  # settings.timezone when unset
    window: Window = Window()
    sections: list[Section] = Field(min_length=1, max_length=20)
    outputs: list[Output] = Field(
        default_factory=lambda: [Output(format="html"), Output(format="csv")], min_length=1
    )

    @field_validator("schedule")
    @classmethod
    def _cron(cls, v: str) -> str:
        try:
            CronTrigger.from_crontab(v)
        except ValueError as exc:
            msg = f"schedule must be a five-field cron expression: {exc}"
            raise ValueError(msg) from exc
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown IANA timezone {v!r}"
            raise ValueError(msg) from exc
        return v


class ReportDocument(Document):
    kind: Literal["Report"]
    metadata: ReportMetadata
    spec: ReportSpec


__all__ = [
    "UPTIME_COLUMNS",
    "Capacity",
    "Events",
    "Output",
    "ReportDocument",
    "ReportSpec",
    "Section",
    "TopN",
    "TrendChart",
    "UptimeTable",
]
