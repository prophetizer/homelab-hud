# SPDX-License-Identifier: Apache-2.0
"""Raw-sample read for the metric widget's sparkline (PLAN.md §8.1: "Cache + samples").

Deliberately minimal: raw tier only, one series, one range, downsampled by bucket average
to a fixed point budget. Tiered ``tier: auto`` selection over rollups is the Phase 2 query
API; this exists so a metric tile can show the last few hours today.
"""

from __future__ import annotations

from sqlalchemy import Engine, select

from hud.store.tables import samples as samples_t
from hud.store.tables import series as series_t

MAX_POINTS = 120


def read_samples(
    engine: Engine, resource_uid: str, metric: str, since: int, until: int
) -> list[tuple[int, float]]:
    """``[(ts, value), …]`` ascending, at most :data:`MAX_POINTS` after bucket averaging."""
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
    points = [(int(ts), float(v)) for ts, v in rows]
    return downsample(points, MAX_POINTS)


def downsample(points: list[tuple[int, float]], budget: int) -> list[tuple[int, float]]:
    if len(points) <= budget:
        return points
    per_bucket = -(-len(points) // budget)  # ceil
    out: list[tuple[int, float]] = []
    for i in range(0, len(points), per_bucket):
        chunk = points[i : i + per_bucket]
        ts = chunk[-1][0]
        out.append((ts, sum(v for _, v in chunk) / len(chunk)))
    return out
