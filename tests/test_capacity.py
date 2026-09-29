# SPDX-License-Identifier: Apache-2.0
"""Storage forecast: a straight line through each disk's used share, a date only when the
history supports one, and the disks that fill soonest first."""

import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, insert

from hud.collector import LiveCache
from hud.config.schemas.board import CapacityDisplay
from hud.models import State, Unit
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import samples, series
from hud.widgets import WidgetEngine
from hud.widgets.series import forecast
from tests.test_widgets_engine import T0, _board, board, metric, res

NOW = int(T0.timestamp())
WEEK = 7 * 86400
HOURLY = [NOW - WEEK + 3600 * i for i in range(24 * 7)]


def test_a_steady_climb_is_dated_with_good_confidence() -> None:
    pts = [(t, 50 + 0.1 * i) for i, t in enumerate(HOURLY)]  # +2.4 % a day
    f = forecast(pts, pts[-1][1], WEEK)
    assert f["verdict"] == "filling" and f["confidence"] == "good"
    assert f["days"] == pytest.approx((100 - pts[-1][1]) / 2.4)


@pytest.mark.parametrize(
    ("values", "verdict"),
    [
        ([60.0] * 168, "steady"),
        ([80 - 0.05 * i for i in range(168)], "shrinking"),
    ],
)
def test_flat_and_shrinking_disks_get_no_date(values: list[float], verdict: str) -> None:
    f = forecast(list(zip(HOURLY, values, strict=True)), values[-1], WEEK)
    assert f["verdict"] == verdict and "days" not in f


def test_too_little_history_is_said_so() -> None:
    few = [(NOW - 600 * i, 50.0 + i) for i in range(5)]
    assert forecast(few, 55.0, WEEK)["verdict"] == "too_little"
    one_day = [(NOW - 86400 + 600 * i, 50 + 0.01 * i) for i in range(144)]
    assert forecast(one_day, 51.4, WEEK)["verdict"] == "too_little"  # under a quarter of a week


def test_noise_without_a_trend_is_unclear_not_a_date() -> None:
    # Deterministic noise, many times larger than a negligible upward drift.
    pts = [
        (t, 50 + 0.0005 * i + 5 * math.sin(i * 1.7) * math.cos(i * 0.31))
        for i, t in enumerate(HOURLY)
    ]
    f = forecast(pts, 50.0, WEEK)
    assert f["verdict"] in ("unclear", "steady")
    assert "days" not in f


def test_error_days_cannot_exceed_warn_days() -> None:
    with pytest.raises(ValidationError):
        CapacityDisplay(warn_days=7, error_days=30)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def _store(db: Engine, uid: str, points: list[tuple[int, float]]) -> None:
    with db.begin() as conn:
        sid = conn.execute(
            insert(series).values(
                provider="glances",
                resource_uid=uid,
                metric="used_pct",
                unit="pct",
                first_seen=points[0][0],
                last_seen=points[-1][0],
            )
        ).inserted_primary_key[0]
        conn.execute(insert(samples), [{"series_id": sid, "ts": t, "value": v} for t, v in points])


DISKS = """
- id: disks
  type: capacity
  grid: { col: 1, row: 1, w: 2 }
  source: { select: { provider: glances, kind: filesystem } }
  display: { window: 7d, warn_days: 30, error_days: 7 }
"""


async def test_disks_are_judged_by_when_they_fill_and_listed_soonest_first(
    config_dir: Path, db: Engine
) -> None:
    tb = 4e12
    filling = [(t, 56 + 0.2 * i) for i, t in enumerate(HOURLY)]  # +4.8 %/day, 89 % now
    _store(db, "glances:filesystem:/media", filling)
    _store(db, "glances:filesystem:/", [(t, 40.0) for t in HOURLY])
    _store(db, "glances:filesystem:/backup", [(t, 96.0) for t in HOURLY])
    cache = LiveCache()
    cache.apply(
        "glances",
        "filesystems",
        [
            res("glances:filesystem:/"),
            res("glances:filesystem:/media"),
            res("glances:filesystem:/backup"),
            res("glances:filesystem:/scratch"),
        ],
        [
            metric("glances:filesystem:/", "used_pct", 40.0, Unit.PCT),
            metric("glances:filesystem:/media", "used_pct", filling[-1][1], Unit.PCT),
            metric("glances:filesystem:/media", "total_bytes", tb, Unit.BYTES),
            metric("glances:filesystem:/media", "free_bytes", tb * 0.1, Unit.BYTES),
            metric("glances:filesystem:/backup", "used_pct", 96.0, Unit.PCT),
        ],
    )
    doc = board(config_dir, _board(DISKS))
    engine = WidgetEngine(cache, db, timezone=lambda: "America/Chicago")
    (w,) = (await engine.resolve_board(doc, T0)).widgets
    items = w.data["items"]
    order = [i["title"] for i in items]
    assert order[0] == "/media"  # fills in about 2 days: down, first
    media = items[0]
    assert media["state"] == "down" and media["forecast"]["verdict"] == "filling"
    assert media["forecast"]["days"] < 7 and media["forecast"]["full_on"] > "2026-09-19"
    assert media["forecast"]["bytes_per_day"] == pytest.approx(4.8 / 100 * tb, rel=1e-6)
    by = {i["title"]: i for i in items}
    assert by["/backup"]["state"] == "degraded"  # steady, but 96 % full
    assert by["/"]["state"] == "up" and by["/"]["forecast"]["verdict"] == "steady"
    assert by["/scratch"]["state"] == "unknown"  # no reading at all
    assert w.state is State.DOWN
    assert engine.referenced_uids(doc) == {i["uid"] for i in items}
