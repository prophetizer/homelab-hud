# SPDX-License-Identifier: Apache-2.0
"""``verify-rollups`` (PLAN R6): recompute stored rollups from what they were built from, and
say where they disagree — so a rollup bug cannot silently turn a report into fiction.

Within a window: every closed 5-minute bucket is recomputed from raw samples, and every
closed hourly bucket from the stored 5-minute buckets — the same arithmetic the rollup
worker uses — and compared field by field (min, max, avg, last, count). Only buckets whose
inputs are all still kept are checked: raw samples last ``retention.samples`` (48 h by
default), so the window is clipped to start a bucket after the oldest raw sample.

Run it by hand::

    python -m hud.store.verify            # the last 24 hours
    python -m hud.store.verify --hours 6

It prints a summary and exits 1 when anything disagrees. The rollup worker also checks the
previous UTC day once a night and logs a WARNING only when something is wrong.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field

from sqlalchemy import Engine, text

from hud.settings import HudEnv
from hud.store.engine import StorePaths, create_store_engine
from hud.store.rollup import GRACE, RUN_SECONDS

_FIELDS = ("min_v", "max_v", "avg_v", "last_v", "n")
_MAX_REPORTED = 20

_FIVE_FROM_SAMPLES = text(
    """
SELECT g.series_id, g.b, MIN(g.value), MAX(g.value), AVG(g.value),
       (SELECT s2.value FROM metrics.samples s2
         WHERE s2.series_id = g.series_id AND s2.ts >= g.b AND s2.ts < g.b + 300
         ORDER BY s2.ts DESC LIMIT 1),
       COUNT(*)
FROM (SELECT series_id, value, (ts / 300) * 300 AS b FROM metrics.samples
       WHERE ts >= :lo AND ts < :hi) g
GROUP BY g.series_id, g.b
"""
)
_HOUR_FROM_FIVE = text(
    """
SELECT g.series_id, g.b, MIN(g.min_v), MAX(g.max_v), SUM(g.avg_v * g.n) / SUM(g.n),
       (SELECT r2.last_v FROM metrics.rollups r2
         WHERE r2.series_id = g.series_id AND r2.tier = '5m'
           AND r2.bucket >= g.b AND r2.bucket < g.b + 3600
         ORDER BY r2.bucket DESC LIMIT 1),
       SUM(g.n)
FROM (SELECT series_id, min_v, max_v, avg_v, n, (bucket / 3600) * 3600 AS b
        FROM metrics.rollups WHERE tier = '5m' AND bucket >= :lo AND bucket < :hi) g
GROUP BY g.series_id, g.b
"""
)
_STORED = text(
    """
SELECT series_id, bucket, min_v, max_v, avg_v, last_v, n FROM metrics.rollups
 WHERE tier = :tier AND bucket >= :lo AND bucket < :hi
"""
)
_OLDEST_SAMPLE = text("SELECT MIN(ts) FROM metrics.samples")
_NAMES = text("SELECT id, resource_uid, metric FROM metrics.series")


@dataclass
class Mismatch:
    series: str  # resource_uid/metric
    tier: str
    bucket: int
    field: str
    stored: float | None
    expected: float | None


@dataclass
class VerifyResult:
    since: int
    until: int
    checked: dict[str, int] = field(default_factory=lambda: {"5m": 0, "1h": 0})
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def summary(self) -> str:
        span = f"{(self.until - self.since) / 3600:.0f} h"
        counts = f"{self.checked['5m']} 5-minute and {self.checked['1h']} hourly buckets"
        if self.ok:
            return f"verify-rollups: {counts} over {span}: all match the data they came from"
        return f"verify-rollups: {counts} over {span}: {len(self.mismatches)} disagree"


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def verify(engine: Engine, since: int, until: int, now: int | None = None) -> VerifyResult:
    """Check closed buckets in [since, until), clipped to what raw samples still cover and
    to what the rollup worker has had a full cycle to write (it runs every 5 minutes)."""
    now = int(time.time()) if now is None else now
    with engine.connect() as conn:
        oldest = conn.execute(_OLDEST_SAMPLE).scalar()
        names = {int(i): f"{u}/{m}" for i, u, m in conn.execute(_NAMES)}
        # A bucket is checkable when all its raw input is kept: start one 5-minute bucket
        # after the oldest sample (the cut is not on a boundary), at an hour for hours.
        # The window's own start is rounded up to a bucket too: a partial first bucket would
        # be recomputed from part of its samples and wrongly disagree with the stored one.
        start = -(-since // 300) * 300
        lo5 = max(start, ((int(oldest) // 300) + 1) * 300) if oldest is not None else until
        hi5 = min(until, ((now - GRACE - int(RUN_SECONDS) - 300) // 300) * 300)
        lo1h = -(-lo5 // 3600) * 3600
        hi1h = (hi5 // 3600) * 3600
        result = VerifyResult(lo5, max(lo5, hi5))
        for tier, query, lo, hi in (
            ("5m", _FIVE_FROM_SAMPLES, lo5, hi5),
            ("1h", _HOUR_FROM_FIVE, lo1h, hi1h),
        ):
            if lo >= hi:
                continue
            expected = {
                (int(r[0]), int(r[1])): r[2:] for r in conn.execute(query, {"lo": lo, "hi": hi})
            }
            stored = {
                (int(r[0]), int(r[1])): r[2:]
                for r in conn.execute(_STORED, {"tier": tier, "lo": lo, "hi": hi})
            }
            result.checked[tier] += len(expected)
            for key in sorted(expected.keys() | stored.keys()):
                want, have = expected.get(key), stored.get(key)
                for i, name in enumerate(_FIELDS):
                    w = want[i] if want is not None else None
                    h = have[i] if have is not None else None
                    if not _same(h, w):
                        result.mismatches.append(
                            Mismatch(names.get(key[0], str(key[0])), tier, key[1], name, h, w)
                        )
                        break  # one line per bucket is enough to find it
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hud.store.verify", description=__doc__)
    parser.add_argument("--hours", type=float, default=24.0, help="how far back to check")
    args = parser.parse_args(argv)
    env = HudEnv()
    engine = create_store_engine(StorePaths(env.data_dir))
    try:
        until = int(time.time())
        result = verify(engine, until - int(args.hours * 3600), until)
    finally:
        engine.dispose()
    print(result.summary())
    for m in result.mismatches[:_MAX_REPORTED]:
        where = f"{m.tier} {m.series} @ {m.bucket}"
        print(f"  {where}: {m.field} stored {m.stored} expected {m.expected}")
    if len(result.mismatches) > _MAX_REPORTED:
        print(f"  … and {len(result.mismatches) - _MAX_REPORTED} more")
    return 0 if result.ok else 1


__all__ = ["Mismatch", "VerifyResult", "main", "verify"]

if __name__ == "__main__":
    sys.exit(main())
