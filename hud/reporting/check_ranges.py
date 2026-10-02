# SPDX-License-Identifier: Apache-2.0
"""Check the query planner on live history (PLAN.md §9.1): for each range from an hour to a
year, which tier ``auto`` reads and how many points come back — at most 1000 always, from a
tier with room for at least 120 — and how much of that tier the history fills.

    docker exec hud python -m hud.reporting.check_ranges [--series uid:metric ...]

Read-only. Without ``--series`` it takes the live series with the longest history.
Fewer than 120 points is the history's doing, never the planner's: it returns every bucket
that holds data, so the table reports how full each range is rather than judging it — and
raw samples' count is the poll rate's. Exit 1 when a range breaks a bound, 2 when there
is nothing to check, else 0.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, select

from hud.reporting.query import MAX_POINTS, MIN_POINTS, pick_tier, read_range, returned_width
from hud.settings import HudEnv
from hud.store.engine import StorePaths, create_store_engine
from hud.store.tables import series as series_t

RANGES: tuple[tuple[str, int], ...] = (
    ("1h", 3600),
    ("6h", 6 * 3600),
    ("24h", 86_400),
    ("7d", 7 * 86_400),
    ("30d", 30 * 86_400),
    ("90d", 90 * 86_400),
    ("1y", 365 * 86_400),
)


@dataclass(frozen=True)
class Row:
    range: str
    tier: str
    points: int
    verdict: str  # ok | FAIL
    note: str = ""


def longest(engine: Engine, count: int, now: int) -> list[tuple[str, str, int, int]]:
    """Up to ``count`` series, longest history first: those seen in the last hour, or any
    when none is (HUD stopped collecting — the history is still worth checking)."""
    query = select(
        series_t.c.resource_uid, series_t.c.metric, series_t.c.first_seen, series_t.c.last_seen
    ).order_by(series_t.c.first_seen, series_t.c.id)
    with engine.connect() as conn:
        rows = conn.execute(query.where(series_t.c.last_seen >= now - 3600).limit(count)).all()
        if not rows:
            print("no series seen in the last hour; checking the longest anyway")
            rows = conn.execute(query.limit(count)).all()
    return [(str(u), str(m), int(f), int(last)) for u, m, f, last in rows]


def seen(engine: Engine, uid: str, metric: str) -> tuple[int, int] | None:
    """The series' first and last sample times."""
    with engine.connect() as conn:
        found = conn.execute(
            select(series_t.c.first_seen, series_t.c.last_seen).where(
                series_t.c.resource_uid == uid, series_t.c.metric == metric
            )
        ).first()
    return (int(found[0]), int(found[1])) if found is not None else None


def returnable(span: int, width: int) -> int:
    """Buckets a range can come back as, after the planner's merging above MAX_POINTS."""
    return max(1, span // returned_width(span, width))


def check(engine: Engine, uid: str, metric: str, since_first: int, now: int) -> list[Row]:
    """The planner's two promises per range: at most MAX_POINTS back, and a tier with room
    for at least MIN_POINTS buckets. How many of those buckets hold data is the history's
    business (a gap is shown, never filled), so it is reported, not judged."""
    out: list[Row] = []
    for name, span in RANGES:
        chosen = pick_tier(span)
        n = len(read_range(engine, (uid, metric), (now - span, now)))
        if chosen is None:
            note = "raw samples: the poll rate sets the count"
        else:
            room = returnable(span, chosen[1])
            note = f"{min(100, round(100 * n / room))}% of {room} buckets hold data"
            merged = returned_width(span, chosen[1])
            if merged != chosen[1]:
                note += f", merged to {merged // 3600}h each"
            if since_first > now - span:
                start = datetime.fromtimestamp(since_first, UTC).date().isoformat()
                note += f" (history starts {start})"
        tier = chosen[0] if chosen else "raw"
        if n > MAX_POINTS:
            out.append(Row(name, tier, n, "FAIL", f"more than {MAX_POINTS}; {note}"))
        elif chosen is not None and span // chosen[1] < MIN_POINTS:
            out.append(Row(name, tier, n, "FAIL", f"tier too coarse for {MIN_POINTS} points"))
        else:
            out.append(Row(name, tier, n, "ok", note))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hud.reporting.check_ranges", description=__doc__
    )
    parser.add_argument("--series", action="append", default=[], metavar="UID:METRIC")
    parser.add_argument("--count", type=int, default=3, help="series to take when none named")
    args = parser.parse_args(argv)
    now = int(time.time())
    engine = create_store_engine(StorePaths(HudEnv().data_dir))
    try:
        picked: list[tuple[str, str, int, int]] = []
        for s in args.series:
            uid, _, metric = s.rpartition(":")
            span = seen(engine, uid, metric) if uid else None
            if span is None:
                print(f"no series {s!r}", file=sys.stderr)
                return 2
            picked.append((uid, metric, *span))
        picked = picked or longest(engine, args.count, now)
        if not picked:
            print("no history to check", file=sys.stderr)
            return 2
        failed = False
        for uid, metric, first, last in picked:
            # As of the last sample: a series HUD stopped collecting is judged on what it has.
            at = min(now, last)
            day = "%Y-%m-%d %H:%M"
            print(
                f"{uid} {metric} (history {datetime.fromtimestamp(first, UTC):{day}}"
                f" to {datetime.fromtimestamp(at, UTC):{day}} UTC)"
            )
            for r in check(engine, uid, metric, first, at):
                failed |= r.verdict == "FAIL"
                print(f"  {r.range:>4}  {r.tier:>3}  {r.points:>5} points  {r.verdict}  {r.note}")
    finally:
        engine.dispose()
    print(
        "FAIL"
        if failed
        else f"planner within bounds: at most {MAX_POINTS} points, tiers with room for {MIN_POINTS}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
