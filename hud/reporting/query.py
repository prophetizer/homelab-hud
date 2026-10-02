# SPDX-License-Identifier: Apache-2.0
"""Query planner: one series over a window, from whichever tier suits it (PLAN.md §9.1).

``tier: auto`` takes the coarsest rollup tier that still gives at least ``MIN_POINTS``
points over the window — raw samples for short windows. Whatever part of the window the
chosen tier does not cover is filled from raw samples, averaged to the tier's width:

- the live edge, newer than the last closed bucket;
- history not rolled up yet — before the rollup worker's first runs, when raw samples
  still reach further back than any rollup.

Where neither has anything, there is nothing: a gap, never an invented value. Rollup
tiers answer with the bucket's average by default, or its min, max or last value.

At most ``MAX_POINTS`` come back (§9.1: "≥ 120 and ≤ 1000 points"): when the span holds
more of the chosen tier's buckets — 90 days of hours, or hours of raw samples — neighbouring
points are merged into wider buckets (a whole number of the tier's width, set by the span):
averages weighted by how many samples each stands for, min of mins, max of maxes, the last
of the lasts.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Connection, Engine, select

from hud.store.tables import rollups as rollups_t
from hud.store.tables import samples as samples_t
from hud.store.tables import series as series_t

Agg = Literal["avg", "min", "max", "last"]
MIN_POINTS = 120
MAX_POINTS = 1000
TIER_WIDTHS: tuple[tuple[str, int], ...] = (("1d", 86_400), ("1h", 3600), ("5m", 300))


def pick_tier(span: int) -> tuple[str, int] | None:
    """The coarsest tier with at least MIN_POINTS buckets over ``span``; None for raw."""
    for tier, width in TIER_WIDTHS:
        if span / width >= MIN_POINTS:
            return tier, width
    return None


def read_range(
    engine: Engine,
    key: tuple[str, str],
    window: tuple[int, int],
    *,
    agg: Agg = "avg",
    tier: str = "auto",
) -> list[tuple[int, float]]:
    """``[(ts, value), …]`` ascending for ``key`` = (resource uid, metric) over ``window`` =
    (since, until), inclusive — see the module docstring."""
    resource_uid, metric = key
    since, until = window
    with engine.connect() as conn:
        sid = conn.execute(
            select(series_t.c.id).where(
                series_t.c.resource_uid == resource_uid, series_t.c.metric == metric
            )
        ).scalar()
        if sid is None:
            return []
        chosen = pick_tier(until - since) if tier == "auto" else _named(tier)
        if chosen is None:
            raw = [(ts, v, 1) for ts, v in _raw(conn, sid, since, until)]
            return _cap(raw, until - since, 1, agg)
        name, width = chosen
        col = {
            "avg": rollups_t.c.avg_v,
            "min": rollups_t.c.min_v,
            "max": rollups_t.c.max_v,
            "last": rollups_t.c.last_v,
        }[agg]
        rows = [
            (int(b), float(v), int(n))
            for b, v, n in conn.execute(
                select(rollups_t.c.bucket, col, rollups_t.c.n)
                .where(
                    rollups_t.c.series_id == sid,
                    rollups_t.c.tier == name,
                    rollups_t.c.bucket >= since - since % width,
                    rollups_t.c.bucket <= until,
                )
                .order_by(rollups_t.c.bucket)
            )
        ]
        head_end = rows[0][0] if rows else until + 1
        tail_start = rows[-1][0] + width if rows else until + 1
        head = (
            _bucketed(_raw(conn, sid, since, head_end - 1), width, agg) if since < head_end else []
        )
        tail = (
            _bucketed(_raw(conn, sid, tail_start, until), width, agg)
            if rows and tail_start <= until
            else []
        )
    return _cap(head + rows + tail, until - since, width, agg)


Weighted = tuple[int, float, int]  # bucket start, value, samples it stands for


def returned_width(span: int, base: int) -> int:
    """The bucket width a range comes back in: ``base`` (the tier's width; 1 for raw) while
    the span holds at most MAX_POINTS of them, else the narrowest whole multiple of it that
    does. Decided by the span alone, so a range always has one resolution, however much
    data there happens to be."""
    if span // base <= MAX_POINTS:
        return base
    merged = -(-span // (MAX_POINTS - 1))  # ceil: at most MAX_POINTS buckets over the span
    return -(-merged // base) * base


def _cap(points: list[Weighted], span: int, base: int, agg: Agg) -> list[tuple[int, float]]:
    """``points`` as (ts, value), merged into :func:`returned_width` buckets when that is
    wider than ``base``."""
    width = returned_width(span, base)
    if width == base:
        return [(ts, v) for ts, v, _ in points]
    groups: dict[int, list[Weighted]] = {}
    for point in points:
        groups.setdefault(point[0] - point[0] % width, []).append(point)
    out: list[tuple[int, float]] = []
    for b in sorted(groups):
        g = groups[b]
        if agg == "avg":
            total = sum(n for _, _, n in g)
            value = sum(v * n for _, v, n in g) / total if total else g[-1][1]
        elif agg == "min":
            value = min(v for _, v, _ in g)
        elif agg == "max":
            value = max(v for _, v, _ in g)
        else:
            value = g[-1][1]
        out.append((b, value))
    return out


def _named(tier: str) -> tuple[str, int] | None:
    for name, width in TIER_WIDTHS:
        if name == tier:
            return name, width
    return None  # "samples", or anything unknown: raw


def _raw(conn: Connection, sid: int, since: int, until: int) -> list[tuple[int, float]]:
    rows = conn.execute(
        select(samples_t.c.ts, samples_t.c.value)
        .where(samples_t.c.series_id == sid, samples_t.c.ts >= since, samples_t.c.ts <= until)
        .order_by(samples_t.c.ts)
    )
    return [(int(ts), float(v)) for ts, v in rows]


def _bucketed(points: Sequence[tuple[int, float]], width: int, agg: Agg) -> list[Weighted]:
    """Raw points folded into ``width`` buckets (bucket start as ts), by ``agg``, each with
    the number of samples in it."""
    groups: dict[int, list[float]] = {}
    for ts, v in points:
        groups.setdefault(ts - ts % width, []).append(v)
    out: list[Weighted] = []
    for b in sorted(groups):
        vs = groups[b]
        value = {
            "avg": sum(vs) / len(vs),
            "min": min(vs),
            "max": max(vs),
            "last": vs[-1],
        }[agg]
        out.append((b, value, len(vs)))
    return out


__all__ = [
    "MAX_POINTS",
    "MIN_POINTS",
    "TIER_WIDTHS",
    "Agg",
    "pick_tier",
    "read_range",
    "returned_width",
]
