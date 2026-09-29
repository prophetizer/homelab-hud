# SPDX-License-Identifier: Apache-2.0
"""Raw-sample read for the metric widget's sparkline (PLAN.md §8.1: "Cache + samples").

One series, one range, downsampled by bucket average to a fixed point budget; the tier is
the query planner's choice (hud.reporting.query).
"""

from __future__ import annotations

from sqlalchemy import Engine

from hud.reporting.query import read_range

MAX_POINTS = 120


def read_samples(
    engine: Engine, resource_uid: str, metric: str, since: int, until: int
) -> list[tuple[int, float]]:
    """``[(ts, value), …]`` ascending, at most :data:`MAX_POINTS` after bucket averaging —
    from the query planner, so a long sparkline still has history once raw samples age
    out (§6.2)."""
    points = read_range(engine, (resource_uid, metric), (since, until))
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
