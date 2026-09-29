# SPDX-License-Identifier: Apache-2.0
"""Rollups and retention (§6.2): buckets carry the right min/max/avg/last, a rerun changes
nothing, a late sample still counts, raw history is only deleted once rolled up, and the
query planner reads the right tier — and raw samples where rollups do not reach yet."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import ColumnElement, Engine, Table, event, func, insert, select

from hud.config.schemas.settings import Retention
from hud.reporting.query import pick_tier, read_range
from hud.store import StorePaths, create_store_engine, rollup, upgrade_all
from hud.store.rollup import RollupWorker
from hud.store.tables import rollups, samples, series

NOW = 1_790_000_000 - 1_790_000_000 % 86_400 + 12 * 3600  # noon UTC
UID, METRIC = "glances:cpu:main", "cpu_pct"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def _seed(db: Engine, days: int = 3, uid: str = UID) -> int:
    """One sample a minute for ``days`` up to NOW; value = minute index."""
    first = NOW - days * 86_400
    with db.begin() as conn:
        sid = conn.execute(
            insert(series).values(
                provider="glances",
                resource_uid=uid,
                metric=METRIC,
                unit="pct",
                first_seen=first,
                last_seen=NOW,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(samples),
            [
                {"series_id": sid, "ts": first + 60 * i, "value": float(i)}
                for i in range(days * 1440)
            ],
        )
    return int(sid)


def _rollup(db: Engine, sid: int, tier: str, bucket: int) -> tuple[float, float, float, float, int]:
    with db.connect() as conn:
        row = conn.execute(
            select(
                rollups.c.min_v, rollups.c.max_v, rollups.c.avg_v, rollups.c.last_v, rollups.c.n
            ).where(rollups.c.series_id == sid, rollups.c.tier == tier, rollups.c.bucket == bucket)
        ).one()
    return (row[0], row[1], row[2], row[3], row[4])


def _count(db: Engine, table: Table, *where: ColumnElement[bool]) -> int:
    with db.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table).where(*where)).scalar_one())


def worker(db: Engine, **keep: str) -> RollupWorker:
    return RollupWorker(db, lambda: Retention(**keep))


def test_buckets_carry_min_max_avg_last_and_count(db: Engine) -> None:
    sid = _seed(db)
    worker(db).run_once(NOW)
    first = NOW - 3 * 86_400
    # The first 5-minute bucket holds minutes 0..4.
    assert _rollup(db, sid, "5m", first) == (0.0, 4.0, 2.0, 4.0, 5)
    # The first hour: minutes 0..59, averaged over its twelve 5-minute buckets by count.
    assert _rollup(db, sid, "1h", first) == (0.0, 59.0, 29.5, 59.0, 60)
    # Days are UTC midnight to midnight; the seed starts at noon, so the first day's bucket
    # holds minutes 0..719 and the next a whole day, 720..2159.
    day = first - first % 86_400
    assert _rollup(db, sid, "1d", day) == (0.0, 719.0, 359.5, 719.0, 720)
    assert _rollup(db, sid, "1d", day + 86_400) == (720.0, 2159.0, 1439.5, 2159.0, 1440)


def test_only_closed_buckets_are_rolled_and_a_rerun_changes_nothing(db: Engine) -> None:
    sid = _seed(db)
    w = worker(db)
    w.run_once(NOW)
    counts = {t: _count(db, rollups, rollups.c.tier == t) for t in ("5m", "1h", "1d")}
    # Up to NOW minus the 60 s grace: the 5-minute bucket still open at NOW is not rolled.
    last_5m = (NOW - 60) // 300 * 300
    with db.connect() as conn:
        newest = conn.execute(
            select(func.max(rollups.c.bucket)).where(rollups.c.tier == "5m")
        ).scalar()
    assert newest == last_5m - 300
    before = _rollup(db, sid, "1h", NOW - 3 * 86_400)
    w.run_once(NOW)
    assert {t: _count(db, rollups, rollups.c.tier == t) for t in counts} == counts
    assert _rollup(db, sid, "1h", NOW - 3 * 86_400) == before


def test_a_long_run_commits_in_slices_and_pauses_between_them(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A first run's backfill must not hold the write lock past the collector's busy
    # timeout: once a transaction has used its budget it commits, and the worker pauses.
    sids = [_seed(db, days=1, uid=f"glances:cpu:{i}") for i in range(4)]
    monkeypatch.setattr(rollup, "TXN_SECONDS", 0.0)
    pauses: list[float] = []
    monkeypatch.setattr(rollup.time, "sleep", pauses.append)
    commits: list[int] = []
    event.listen(db, "commit", lambda _conn: commits.append(1))
    worker(db).run_once(NOW)
    # One transaction per series (a zero budget still does one), a pause between each.
    assert len(pauses) == len(sids) and set(pauses) == {rollup.PAUSE_SECONDS}
    assert len(commits) >= len(sids)
    for sid in sids:
        assert _rollup(db, sid, "1h", NOW - 86_400)[4] == 60  # an hour of minutes


def test_a_late_sample_is_counted_on_the_next_run(db: Engine) -> None:
    sid = _seed(db, days=1)
    w = worker(db)
    w.run_once(NOW)
    bucket = (NOW - 60) // 300 * 300 - 300  # the newest rolled 5-minute bucket
    _min, _max, _avg, _last, n = _rollup(db, sid, "5m", bucket)
    with db.begin() as conn:
        conn.execute(insert(samples).values(series_id=sid, ts=bucket + 1, value=-100.0))
    w.run_once(NOW)
    assert _rollup(db, sid, "5m", bucket)[0] == -100.0
    assert _rollup(db, sid, "5m", bucket)[4] == n + 1


def test_raw_ages_out_after_48h_but_never_before_its_rollup(db: Engine) -> None:
    sid = _seed(db)
    worker(db).run_once(NOW)
    assert _count(db, samples, samples.c.ts < NOW - 48 * 3600) == 0
    assert _count(db, samples, samples.c.ts >= NOW - 48 * 3600) > 0
    # Everything deleted is held in 5m buckets.
    assert _count(db, rollups, rollups.c.tier == "5m", rollups.c.bucket < NOW - 48 * 3600) > 0

    # A retention shorter than the open bucket still keeps what is not rolled yet.
    sid2 = sid  # same series: the newest raw minutes are past the last closed bucket
    worker(db, samples="1s").run_once(NOW)
    newest_rolled_end = (NOW - 60) // 300 * 300
    assert _count(db, samples, samples.c.series_id == sid2, samples.c.ts >= newest_rolled_end) > 0


def test_rollup_tiers_age_out_by_their_own_retention(db: Engine) -> None:
    _seed(db)
    worker(db, rollup_5m="1d").run_once(NOW)
    # 5m buckets older than a day are gone — hours still hold them.
    assert _count(db, rollups, rollups.c.tier == "5m", rollups.c.bucket < NOW - 86_400) == 0
    assert _count(db, rollups, rollups.c.tier == "1h", rollups.c.bucket < NOW - 86_400) > 0


@pytest.mark.parametrize(
    ("span", "tier"),
    [(3600, None), (6 * 3600, None), (86_400, "5m"), (7 * 86_400, "1h"), (400 * 86_400, "1d")],
)
def test_the_coarsest_tier_with_enough_points(span: int, tier: str | None) -> None:
    got = pick_tier(span)
    assert (got[0] if got else None) == tier


def test_before_any_rollup_a_week_reads_raw_folded_into_hours(db: Engine) -> None:
    _seed(db)
    points = read_range(db, (UID, METRIC), (NOW - 7 * 86_400, NOW))
    assert len(points) == 72  # three days of raw, one point an hour
    assert points[0] == (NOW - 3 * 86_400, 29.5)


def test_a_week_reads_hours_then_the_live_edge_from_raw(db: Engine) -> None:
    _seed(db)
    worker(db).run_once(NOW)
    points = read_range(db, (UID, METRIC), (NOW - 7 * 86_400, NOW))
    stamps = [t for t, _ in points]
    assert stamps == sorted(stamps) and len(stamps) == len(set(stamps))
    assert all(t % 3600 == 0 for t in stamps)
    assert stamps[-1] >= NOW - 3600  # the current hour comes from raw
    peak = read_range(db, (UID, METRIC), (NOW - 7 * 86_400, NOW), agg="max")
    assert peak[0][1] == 59.0  # the first hour's highest minute
