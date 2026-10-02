# SPDX-License-Identifier: Apache-2.0
"""The range check (PLAN §9.1 on live history): every range from an hour to a year within
1000 points, from a tier with room for 120; how full the history is gets reported, not
judged — the planner returns every bucket that holds data."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from hud.reporting.check_ranges import check
from hud.store import StorePaths, create_store_engine, upgrade_all
from tests.test_rollups import METRIC, NOW, UID, _seed, worker


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def test_three_days_of_history_pass_and_say_how_full_each_range_is(db: Engine) -> None:
    _seed(db)
    worker(db).run_once(NOW)
    rows = {r.range: r for r in check(db, UID, METRIC, NOW - 3 * 86_400, NOW)}
    assert all(r.verdict == "ok" for r in rows.values()), rows
    assert rows["1h"].tier == "raw" and "poll rate" in rows["1h"].note
    assert rows["24h"].tier == "5m" and rows["24h"].points == 288
    assert rows["24h"].note == "100% of 288 buckets hold data"
    assert rows["7d"].tier == "1h" and rows["7d"].note.startswith("43% of 168 buckets")
    assert "history starts" in rows["7d"].note
    assert all(r.points <= 1000 for r in rows.values())


def test_a_tier_too_coarse_for_120_points_fails(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db)
    monkeypatch.setattr("hud.reporting.check_ranges.pick_tier", lambda _span: ("1d", 86_400))
    rows = {r.range: r for r in check(db, UID, METRIC, NOW - 3 * 86_400, NOW)}
    assert rows["7d"].verdict == "FAIL" and "too coarse" in rows["7d"].note
    assert rows["1y"].verdict == "ok"  # 365 days of 1d buckets is room enough
