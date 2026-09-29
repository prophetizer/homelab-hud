# SPDX-License-Identifier: Apache-2.0
"""Rollup worker and retention (PLAN.md §6.2).

Every five minutes, per series: raw samples roll up into closed 5-minute buckets, those
into hours, hours into days; then rows older than their tier's retention are deleted —
but only once the tier above holds them — and freed pages are handed back to the disk
(``incremental_vacuum``; metrics.db is created with ``auto_vacuum = INCREMENTAL``).

- **Per series, along the primary key.** Samples are keyed ``(series_id, ts)`` and rollups
  ``(series_id, tier, bucket)``, so every read and delete is a range on the key; nothing
  scans a whole table.
- **Restart-safe and idempotent.** A series resumes from its own last bucket in each tier
  (recomputing that bucket, so late samples count) with ``INSERT … ON CONFLICT DO
  UPDATE``; a crashed run recomputes cleanly. The first run backfills from the oldest
  sample.
- **Buckets are UTC.** A local day for a report composes from hours.
- **Short transactions.** Series are processed in batches, each its own transaction, so
  the collector's writes never wait long behind a run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, select, text

from hud.config.schemas.duration import parse_duration
from hud.config.schemas.settings import Retention
from hud.store.tables import METRICS_SCHEMA
from hud.store.tables import series as series_t

log = logging.getLogger(__name__)

RUN_SECONDS = 300.0
# Each write transaction stops taking series once it has run this long, then the worker
# pauses so a collector write waiting on the lock gets in. A count of series was not enough:
# a first run's backfill held 25 heavy series past the collector's 5 s busy timeout. The
# pause outlasts SQLite's longest busy-handler sleep (100 ms), so a waiter always retries
# inside it.
TXN_SECONDS = 0.25
PAUSE_SECONDS = 0.1
GRACE = 60  # seconds a sample may arrive after its bucket closes and still be counted
VACUUM_PAGES = 2000  # pages handed back per run, so no run holds the file long

# (tier, width in seconds, source): each tier is built from the one before it.
TIERS: tuple[tuple[str, int, str], ...] = (
    ("5m", 300, "samples"),
    ("1h", 3600, "5m"),
    ("1d", 86_400, "1h"),
)

# Plain SQL literals, the attached metrics.db named outright: nothing is interpolated.
assert METRICS_SCHEMA == "metrics"  # the literals below name it

_FROM_SAMPLES = text(
    """
INSERT INTO metrics.rollups (series_id, tier, bucket, min_v, max_v, avg_v, last_v, n)
SELECT g.series_id, :tier, g.b, MIN(g.value), MAX(g.value), AVG(g.value),
       (SELECT s2.value FROM metrics.samples s2
         WHERE s2.series_id = g.series_id AND s2.ts >= g.b AND s2.ts < g.b + :w
         ORDER BY s2.ts DESC LIMIT 1),
       COUNT(*)
FROM (SELECT series_id, value, (ts / :w) * :w AS b FROM metrics.samples
       WHERE series_id = :sid AND ts >= :lo AND ts < :hi) g
WHERE true
GROUP BY g.series_id, g.b
ON CONFLICT (series_id, tier, bucket) DO UPDATE SET
  min_v = excluded.min_v, max_v = excluded.max_v, avg_v = excluded.avg_v,
  last_v = excluded.last_v, n = excluded.n
"""
)
_FROM_ROLLUPS = text(
    """
INSERT INTO metrics.rollups (series_id, tier, bucket, min_v, max_v, avg_v, last_v, n)
SELECT g.series_id, :tier, g.b, MIN(g.min_v), MAX(g.max_v), SUM(g.avg_v * g.n) / SUM(g.n),
       (SELECT r2.last_v FROM metrics.rollups r2
         WHERE r2.series_id = g.series_id AND r2.tier = :src
           AND r2.bucket >= g.b AND r2.bucket < g.b + :w
         ORDER BY r2.bucket DESC LIMIT 1),
       SUM(g.n)
FROM (SELECT series_id, min_v, max_v, avg_v, n, (bucket / :w) * :w AS b FROM metrics.rollups
       WHERE series_id = :sid AND tier = :src AND bucket >= :lo AND bucket < :hi) g
WHERE true
GROUP BY g.series_id, g.b
ON CONFLICT (series_id, tier, bucket) DO UPDATE SET
  min_v = excluded.min_v, max_v = excluded.max_v, avg_v = excluded.avg_v,
  last_v = excluded.last_v, n = excluded.n
"""
)
_LAST_BUCKET = text(
    "SELECT MAX(bucket) FROM metrics.rollups WHERE series_id = :sid AND tier = :tier"
)
_FIRST_SAMPLE = text("SELECT MIN(ts) FROM metrics.samples WHERE series_id = :sid")
_FIRST_BUCKET = text(
    "SELECT MIN(bucket) FROM metrics.rollups WHERE series_id = :sid AND tier = :tier"
)
_DELETE_SAMPLES = text("DELETE FROM metrics.samples WHERE series_id = :sid AND ts < :before")
_DELETE_ROLLUPS = text(
    "DELETE FROM metrics.rollups WHERE series_id = :sid AND tier = :tier AND bucket < :before"
)
_VACUUM = f"PRAGMA metrics.incremental_vacuum({VACUUM_PAGES})"
_CHECKPOINT = "PRAGMA metrics.wal_checkpoint(TRUNCATE)"


@dataclass
class RunStats:
    series: int = 0
    buckets: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


def _seconds(value: str) -> int:
    """A retention as seconds; 0 means keep forever."""
    return parse_duration(value)


class RollupWorker:
    def __init__(self, engine: Engine, retention: Callable[[], Retention]) -> None:
        self.engine = engine
        self.retention = retention

    async def run(self) -> None:
        while True:
            try:
                stats = await asyncio.to_thread(self.run_once)
                log.info(
                    "rollups: %d series, buckets %s, deleted %s in %.1fs",
                    stats.series,
                    stats.buckets,
                    stats.deleted,
                    stats.seconds,
                )
            except Exception:
                log.exception("rollups: run failed; retrying next cycle")
            await asyncio.sleep(RUN_SECONDS)

    def run_once(self, now: int | None = None) -> RunStats:
        started = time.monotonic()
        now = int(time.time()) if now is None else now
        keep = self.retention()
        horizon = {
            "samples": _seconds(keep.samples),
            "5m": _seconds(keep.rollup_5m),
            "1h": _seconds(keep.rollup_1h),
            "1d": _seconds(keep.rollup_1d),
        }
        stats = RunStats(
            buckets=dict.fromkeys((t for t, _, _ in TIERS), 0),
            deleted=dict.fromkeys(horizon, 0),
        )
        with self.engine.connect() as conn:
            ids = [int(i) for i in conn.execute(select(series_t.c.id)).scalars()]
        stats.series = len(ids)
        todo = iter(ids)
        pending = True
        while pending:
            with self.engine.begin() as conn:
                began = time.monotonic()
                for sid in todo:  # at least one series per transaction
                    self._series(conn, sid, now, horizon, stats)
                    if time.monotonic() - began >= TXN_SECONDS:
                        break
                else:
                    pending = False
            if pending:
                time.sleep(PAUSE_SECONDS)
        with self.engine.begin() as conn:
            conn.exec_driver_sql(_VACUUM)
        # Hand the write-ahead log back too: a backfill's large transactions would otherwise
        # leave it at its largest size. Busy readers just make this a no-op for the run.
        with self.engine.connect() as conn:
            conn.exec_driver_sql(_CHECKPOINT)
        stats.seconds = time.monotonic() - started
        return stats

    def _series(
        self, conn: Connection, sid: int, now: int, horizon: dict[str, int], stats: RunStats
    ) -> None:
        # Roll up, finest first; each tier only as far as the one below it is closed.
        closed = {"samples": now - GRACE}
        for tier, w, src in TIERS:
            hi = (closed[src] // w) * w
            closed[tier] = hi
            last = conn.execute(_LAST_BUCKET, {"sid": sid, "tier": tier}).scalar()
            if last is not None:
                lo = int(last)  # recompute the last bucket: late samples count
            else:
                first = conn.execute(
                    _FIRST_SAMPLE if src == "samples" else _FIRST_BUCKET,
                    {"sid": sid} if src == "samples" else {"sid": sid, "tier": src},
                ).scalar()
                if first is None:
                    continue
                lo = (int(first) // w) * w
            if lo >= hi:
                continue
            stmt = _FROM_SAMPLES if src == "samples" else _FROM_ROLLUPS
            params = {"tier": tier, "w": w, "sid": sid, "lo": lo, "hi": hi}
            if src != "samples":
                params["src"] = src
            stats.buckets[tier] += conn.execute(stmt, params).rowcount or 0
        # Age out — never past what the tier above already holds.
        above = {"samples": "5m", "5m": "1h", "1h": "1d", "1d": None}
        for tier, keep in horizon.items():
            if keep <= 0:
                continue  # forever
            before = now - keep
            parent = above[tier]
            if parent is not None:
                before = min(before, closed.get(parent, 0))
            if tier == "samples":
                deleted = conn.execute(_DELETE_SAMPLES, {"sid": sid, "before": before}).rowcount
            else:
                deleted = conn.execute(
                    _DELETE_ROLLUPS, {"sid": sid, "tier": tier, "before": before}
                ).rowcount
            stats.deleted[tier] += deleted or 0


__all__ = ["RUN_SECONDS", "TIERS", "RollupWorker", "RunStats"]
