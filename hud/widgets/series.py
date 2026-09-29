# SPDX-License-Identifier: Apache-2.0
"""Series reads for the chart and heatmap widgets (PLAN.md §6, §8.1).

Charts need every series on one time axis, so points are averaged into equal buckets over
the window — a bucket with no sample is None (a gap, never a zero). The heatmap folds a
window into weekday-by-hour cells in settings.timezone.

Reads go through the query planner (hud.reporting.query): raw samples for short windows,
rollups for long ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo

from sqlalchemy import Engine

from hud.reporting.query import pick_tier, read_range

CHART_BUCKETS = 240


def read_points(
    engine: Engine, resource_uid: str, metric: str, since: int, until: int
) -> list[tuple[int, float]]:
    """A series over [since, until] from the query planner: raw samples for short windows,
    rollups for long ones, raw filling whatever the rollups do not reach yet (§6.2)."""
    return read_range(engine, (resource_uid, metric), (since, until))


def chart_buckets(span: int) -> int:
    """How many buckets a chart over ``span`` can fill without gaps: CHART_BUCKETS, or the
    number of rollup buckets when the planner reads a tier coarser than that."""
    tier = pick_tier(span)
    return min(CHART_BUCKETS, max(1, span // tier[1])) if tier else CHART_BUCKETS


def bucket_axis(since: int, until: int, n: int = CHART_BUCKETS) -> list[int]:
    """Bucket start times: n equal buckets over [since, until)."""
    width = (until - since) / n
    return [int(since + i * width) for i in range(n)]


def bucketed(
    points: Sequence[tuple[int, float]], since: int, until: int, n: int = CHART_BUCKETS
) -> list[float | None]:
    """The mean of the samples in each bucket, or None where there are none."""
    width = (until - since) / n
    sums = [0.0] * n
    counts = [0] * n
    for ts, v in points:
        if since <= ts < until:
            i = min(n - 1, int((ts - since) / width))
            sums[i] += v
            counts[i] += 1
    return [s / c if c else None for s, c in zip(sums, counts, strict=True)]


def heat(
    points: Sequence[tuple[int, float]], tz: tzinfo, agg: str = "mean"
) -> list[list[float | None]]:
    """7 rows (Monday first) by 24 hours in ``tz``: each cell the mean (or max) of every
    sample that fell in that weekday and hour across the window; None when none did."""
    acc: list[list[list[float]]] = [[[] for _ in range(24)] for _ in range(7)]
    for ts, v in points:
        local = datetime.fromtimestamp(ts, tz)
        acc[local.weekday()][local.hour].append(v)
    return [
        [(max(vs) if agg == "max" else sum(vs) / len(vs)) if vs else None for vs in hours]
        for hours in acc
    ]


MIN_POINTS = 12
FLAT_PCT_PER_DAY = 0.01  # less growth than this is steady, not filling
CLEAR_R2 = 0.3  # below this the line explains too little to date anything by it
GOOD_R2 = 0.7


def fit(points: Sequence[tuple[int, float]]) -> tuple[float, float, float]:
    """Least squares: (slope per second, intercept, r²). Needs two distinct times."""
    n = len(points)
    mx = sum(t for t, _ in points) / n
    my = sum(v for _, v in points) / n
    sxx = sum((t - mx) ** 2 for t, _ in points)
    sxy = sum((t - mx) * (v - my) for t, v in points)
    syy = sum((v - my) ** 2 for _, v in points)
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    r2 = (sxy * sxy) / (sxx * syy) if sxx and syy else (1.0 if not syy else 0.0)
    return slope, intercept, r2


def forecast(
    points: Sequence[tuple[int, float]], current: float, window_seconds: int
) -> dict[str, object]:
    """When a used share (0-100) reaches 100, from a straight line through ``points``.

    Honest about what it cannot say: ``too_little`` history (fewer than 12 samples, or
    spanning under a quarter of the window), ``unclear`` (the line explains under 30 % of
    the movement), ``steady`` or ``shrinking``. Only ``filling`` carries a date, with its
    confidence: ``good`` (r² ≥ 0.7) or ``rough``."""
    span = points[-1][0] - points[0][0] if len(points) >= 2 else 0
    base: dict[str, object] = {"points": len(points), "span_days": span / 86400}
    if len(points) < MIN_POINTS or span < window_seconds / 4:
        return {**base, "verdict": "too_little"}
    slope, _, r2 = fit(points)
    per_day = slope * 86400
    base |= {"pct_per_day": per_day, "r2": r2}
    if abs(per_day) < FLAT_PCT_PER_DAY:
        return {**base, "verdict": "steady"}
    if per_day < 0:
        return {**base, "verdict": "shrinking"}
    if r2 < CLEAR_R2:
        return {**base, "verdict": "unclear"}
    days = max(0.0, (100.0 - current) / per_day)
    return {
        **base,
        "verdict": "filling",
        "days": days,
        "confidence": "good" if r2 >= GOOD_R2 else "rough",
    }


__all__ = [
    "CHART_BUCKETS",
    "bucket_axis",
    "bucketed",
    "chart_buckets",
    "fit",
    "forecast",
    "heat",
    "read_points",
]
