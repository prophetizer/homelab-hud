# SPDX-License-Identifier: Apache-2.0
"""Series reads for the chart and heatmap widgets (PLAN.md §6, §8.1).

Charts need every series on one time axis, so points are averaged into equal buckets over
the window — a bucket with no sample is None (a gap, never a zero). The heatmap folds a
window into weekday-by-hour cells in settings.timezone.

Raw samples only: until the rollup worker exists (§6.2), a 7-day chart reads a week of raw
samples. Bounded by the widget's range and a single indexed series each.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo

from sqlalchemy import Engine, select

from hud.store.tables import samples as samples_t
from hud.store.tables import series as series_t

CHART_BUCKETS = 240


def read_points(
    engine: Engine, resource_uid: str, metric: str, since: int, until: int
) -> list[tuple[int, float]]:
    """Every raw sample in [since, until], ascending."""
    with engine.connect() as conn:
        sid = conn.execute(
            select(series_t.c.id).where(
                series_t.c.resource_uid == resource_uid, series_t.c.metric == metric
            )
        ).scalar()
        if sid is None:
            return []
        rows = conn.execute(
            select(samples_t.c.ts, samples_t.c.value)
            .where(samples_t.c.series_id == sid, samples_t.c.ts >= since, samples_t.c.ts <= until)
            .order_by(samples_t.c.ts)
        ).all()
    return [(int(ts), float(v)) for ts, v in rows]


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


__all__ = ["CHART_BUCKETS", "bucket_axis", "bucketed", "heat", "read_points"]
