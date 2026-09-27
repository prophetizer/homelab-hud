# SPDX-License-Identifier: Apache-2.0
"""``kind: Board`` — a grid of widgets (PLAN.md §8.2).

Widgets discriminate on ``type``. Phase 1 renders ``static``, ``resource``, ``list``,
``metric`` and ``embed``; the other types in the §8.1 taxonomy validate as
:class:`UnsupportedWidget` and render as an honest "not available yet" tile rather than
failing the whole board. A board from a newer image therefore still loads on an older one
(PLAN.md §11.3), and an unknown type degrades one tile, never the dashboard (invariant 6).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

from hud.config.schemas.base import Document, Metadata
from hud.config.schemas.settings import parse_duration
from hud.models.enums import State

PHASE1_WIDGET_TYPES: frozenset[str] = frozenset(
    {"static", "resource", "list", "metric", "embed", "bars", "uptime"}
)
# Known from the taxonomy but implemented in a later phase. Anything else is simply unknown.
LATER_WIDGET_TYPES: dict[str, str] = {
    "chart": "Phase 2",
    "report": "Phase 2",
    "action": "Phase 3",
    "composite": "Phase 3",
}


# metric.<name> sorts by the latest value (e.g. "-metric.cpu_pct" for the busiest first).
# Added 2026-09-26 (brought forward from Phase 2): relaxes a validator, dashboard/v1-compatible.
_SORT_KEY = re.compile(
    r"-?(name|state|state_severity|kind|provider|attrs\.[A-Za-z0-9_.-]+|metric\.[A-Za-z0-9_]+)"
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------- layout


class Columns(_Spec):
    sm: int = Field(default=1, ge=1, le=12)
    md: int = Field(default=2, ge=1, le=12)
    lg: int = Field(default=4, ge=1, le=12)


class Layout(_Spec):
    columns: Columns = Columns()
    gap: int = Field(default=12, ge=0, le=64)


class Grid(BaseModel):
    """1-based cell position on the ``lg`` grid; smaller breakpoints reflow in the SPA."""

    model_config = ConfigDict(extra="forbid")

    col: int = Field(ge=1, le=12)
    row: int = Field(ge=1)
    w: int = Field(default=1, ge=1, le=12)
    h: int = Field(default=1, ge=1, le=24)


# ----------------------------------------------------------------------------- sources


class Link(_Spec):
    title: str
    url: str


class StaticDisplay(_Spec):
    text: str | None = None
    links: list[Link] = Field(default_factory=list)


class ResourceSource(_Spec):
    resource: str  # canonical uid


class Select(_Spec):
    provider: str | list[str] | None = None
    kind: str | list[str] | None = None
    state: State | list[State] | None = None
    label: dict[str, str] | None = None  # matches provider metadata.labels
    # Matches the resource's own attributes, dotted paths, all must be equal — e.g.
    # {"homepage.group": "Media Links"}. Added 2026-09-25, optional: dashboard/v1-compatible.
    attrs: dict[str, str] | None = None


class ListSource(_Spec):
    select: Select = Select()
    sort: list[str] = Field(default_factory=lambda: ["-state_severity", "name"])
    limit: int | None = Field(default=None, ge=1, le=500)

    @field_validator("sort")
    @classmethod
    def _sort_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            if not _SORT_KEY.fullmatch(key):
                msg = f"unsupported sort key {key!r}"
                raise ValueError(msg)
        return v


class ListDisplay(_Spec):
    fields: list[str] = Field(default_factory=lambda: ["name", "state"])
    empty_text: str = "Nothing to show"
    # Which field titles each row — e.g. attrs.homepage.name, so a container shows as
    # "Agregarr" rather than "agregarr". Falls back to the resource name when unset or
    # empty. Added 2026-09-26, optional: dashboard/v1-compatible (§11.3a).
    title: str | None = None
    # A percentage field (0-100) drawn as a usage bar on each row, coloured by the row's
    # state — e.g. metric.used_pct on filesystems. Added 2026-09-26, optional.
    bar: str | None = None
    # rows: one line per resource. grid: a wall of status squares, one per resource, for
    # "is anything wrong among these 117" at a glance. cards: a card per resource — an app
    # (icon, name, description) or, when it has a value, a reading (21.5 °C). Added
    # 2026-09-26, optional.
    layout: Literal["rows", "grid", "cards", "media"] = "rows"
    # A field holding an icon name (attrs.homepage.icon, or `provider` for a service
    # named after its provider). Served by /api/v1/icons. Added 2026-09-26, optional.
    icon: str | None = None
    # Show each row's attrs.image (a poster), served by /api/v1/images through the row's
    # own provider so its credentials stay server-side. Added 2026-09-26, optional.
    image: bool = False
    # A 24 h availability strip on each row (hourly cells) and the day's uptime %, from
    # the availability history. Added 2026-09-27, optional: dashboard/v1-compatible.
    uptime: bool = False


class MetricSource(_Spec):
    metric: str
    resource: str


class Format(_Spec):
    unit: str | None = None  # display unit override; storage unit is canonical
    precision: int = Field(default=1, ge=0, le=6)


class Sparkline(_Spec):
    range: str = "6h"
    tier: Literal["auto", "samples", "5m", "1h", "1d"] = "auto"

    @field_validator("range")
    @classmethod
    def _dur(cls, v: str) -> str:
        parse_duration(v)
        return v


class Threshold(_Spec):
    gte: float | None = None
    lte: float | None = None
    state: Literal["warn", "error"]

    @model_validator(mode="after")
    def _one_bound(self) -> Self:
        if (self.gte is None) == (self.lte is None):
            msg = "exactly one of gte or lte is required"
            raise ValueError(msg)
        return self


class HeroStat(_Spec):
    """One reading in a hero strip: a metric of any resource, labelled, judged by its own
    thresholds. A percentage draws as a small gauge; anything else as a number."""

    resource: str
    metric: str
    label: str | None = None
    thresholds: list[Threshold] = Field(default_factory=list)


class ResourceDisplay(_Spec):
    fields: list[str] = Field(default_factory=lambda: ["state", "name"])
    # fields: a label/value list. hero: a wide strip — the resource's name large, its
    # fields as a subtitle, and `stats` as a row of readings. Added 2026-09-26, optional.
    style: Literal["fields", "hero"] = "fields"
    stats: list[HeroStat] = Field(default_factory=list)


class MetricRef(_Spec):
    metric: str
    resource: str | None = None  # defaults to the widget's own resource


class MetricDisplay(_Spec):
    format: Format = Format()
    sparkline: Sparkline | None = None
    thresholds: list[Threshold] = Field(default_factory=list)
    # The whole the value is part of — used_bytes of total_bytes — shown as "70.6 / 128 GiB"
    # with a usage bar. When set, thresholds compare against the percentage of the total,
    # not the raw value. Added 2026-09-26, optional: dashboard/v1-compatible.
    total: MetricRef | None = None
    # number: the value large. gauge: a status-coloured dial for a percentage — the value
    # itself when its unit is pct, else its share of `total`; anything else stays a
    # number. Added 2026-09-26, optional: dashboard/v1-compatible.
    style: Literal["number", "gauge"] = "number"
    # Show the change against this long ago (↑ 12 % vs 1h), from stored samples. Unset:
    # no delta. Added 2026-09-27, optional: dashboard/v1-compatible.
    delta: Literal["15m", "1h", "6h", "24h"] | None = None


class BarsSource(ListSource):
    metric: str  # drawn for every selected resource


class BarsDisplay(_Spec):
    # columns: a strip of small vertical bars (cores); rows: labelled horizontal bars.
    layout: Literal["columns", "rows"] = "rows"
    title: str | None = None  # row label field, as ListDisplay.title
    format: Format = Format()
    # Bars are coloured by these (each bar's own value); no threshold crossed is "up".
    thresholds: list[Threshold] = Field(default_factory=list)
    # The headline number over the bars: the mean or max of the drawn values.
    summary: Literal["mean", "max"] | None = None
    # Full scale. Unset: 100 for percentages (or the largest value, if higher), otherwise
    # the largest value.
    max: float | None = Field(default=None, gt=0)
    empty_text: str = "Nothing to show"


class UptimeSource(ListSource):
    """One resource (``resource:``, as PLAN §8 shows it) or a selection of them."""

    resource: str | None = None


UptimeRange = Literal["24h", "7d", "30d", "90d"]


class UptimeDisplay(_Spec):
    range: UptimeRange = "24h"
    buckets: int = Field(default=48, ge=6, le=120)
    show_sla: bool = True
    # The SLA % over a different window than the bars: 24 h of cells beside a 30-day %.
    sla_range: UptimeRange | None = None
    # §6.3: unknown and not-observed time is left out of the ratio. Off: it counts as down.
    exclude_unknown: bool = True
    title: str | None = None  # row title field, as ListDisplay.title
    empty_text: str = "Nothing to show"


class EmbedSource(_Spec):
    url: str
    sandbox: Literal["strict", "relaxed"] = "strict"

    @field_validator("url")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not re.match(r"^https?://", v):
            msg = "embed url must be absolute http(s)"
            raise ValueError(msg)
        return v


class EmbedDisplay(_Spec):
    open_in: Literal["workspace", "inline"] = "inline"
    fallback: Literal["card", "new_tab"] = "card"


# ----------------------------------------------------------------------------- widgets


class _Widget(_Spec):
    id: str
    title: str | None = None
    grid: Grid
    # The tile's header icon (an /api/v1/icons name, or "none"). Unset: the icon of the one
    # provider the tile shows, if it shows one. For an embed, also its sidebar icon.
    # Added 2026-09-26, optional: dashboard/v1-compatible.
    icon: str | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", v):
            msg = "widget id must be [A-Za-z0-9_-]"
            raise ValueError(msg)
        return v


class StaticWidget(_Widget):
    type: Literal["static"]
    display: StaticDisplay = StaticDisplay()


class ResourceWidget(_Widget):
    type: Literal["resource"]
    source: ResourceSource
    display: ResourceDisplay = ResourceDisplay()


class ListWidget(_Widget):
    type: Literal["list"]
    source: ListSource = ListSource()
    display: ListDisplay = ListDisplay()


class MetricWidget(_Widget):
    type: Literal["metric"]
    source: MetricSource
    display: MetricDisplay = MetricDisplay()


class BarsWidget(_Widget):
    """One metric across many resources: per-core CPU, busiest containers. Added
    2026-09-26; a new widget type is dashboard/v1-compatible (§11.3a)."""

    type: Literal["bars"]
    source: BarsSource
    display: BarsDisplay = BarsDisplay()


class UptimeWidget(_Widget):
    """Availability history as a strip of cells per resource, and its SLA % (PLAN §8,
    §6.3). Implemented 2026-09-27 (Phase 2 slice 1); the type was reserved from v1."""

    type: Literal["uptime"]
    source: UptimeSource
    display: UptimeDisplay = UptimeDisplay()


class EmbedWidget(_Widget):
    type: Literal["embed"]
    source: EmbedSource
    display: EmbedDisplay = EmbedDisplay()


class UnsupportedWidget(_Widget):
    """Any type this build cannot render. Kept verbatim so write-back preserves it."""

    type: str
    source: dict[str, Any] | None = None
    display: dict[str, Any] | None = None

    @property
    def reason(self) -> str:
        phase = LATER_WIDGET_TYPES.get(self.type)
        if phase:
            return f"widget type '{self.type}' arrives in {phase}"
        return f"unknown widget type '{self.type}'"


def _widget_tag(value: Any) -> str:  # noqa: ANN401
    t = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    return t if isinstance(t, str) and t in PHASE1_WIDGET_TYPES else "unsupported"


Widget = Annotated[
    Annotated[StaticWidget, Tag("static")]
    | Annotated[ResourceWidget, Tag("resource")]
    | Annotated[ListWidget, Tag("list")]
    | Annotated[MetricWidget, Tag("metric")]
    | Annotated[EmbedWidget, Tag("embed")]
    | Annotated[BarsWidget, Tag("bars")]
    | Annotated[UptimeWidget, Tag("uptime")]
    | Annotated[UnsupportedWidget, Tag("unsupported")],
    Discriminator(_widget_tag),
]


# ----------------------------------------------------------------------------- document


class BoardMetadata(Metadata):
    name: str
    icon: str | None = None
    visible_to: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            msg = "board name must be lowercase [a-z0-9_-] (it is the URL slug)"
            raise ValueError(msg)
        return v


class BoardSpec(_Spec):
    layout: Layout = Layout()
    widgets: list[Widget] = Field(default_factory=list)

    @field_validator("widgets")
    @classmethod
    def _unique_ids(cls, v: list[Widget]) -> list[Widget]:
        ids = [w.id for w in v]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            msg = f"duplicate widget ids: {', '.join(dupes)}"
            raise ValueError(msg)
        return v


class BoardDocument(Document):
    kind: Literal["Board"]
    metadata: BoardMetadata
    spec: BoardSpec = BoardSpec()

    @property
    def unsupported_widgets(self) -> list[UnsupportedWidget]:
        return [w for w in self.spec.widgets if isinstance(w, UnsupportedWidget)]
