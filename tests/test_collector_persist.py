# SPDX-License-Identifier: Apache-2.0
"""StoreWriter: one transaction per poll, series upsert, OR REPLACE samples, events."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from hud.collector import Normalized, StoreWriter
from hud.models import Event, Metric, Severity, Unit
from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import events as events_t
from hud.store.tables import samples as samples_t
from hud.store.tables import series as series_t

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    paths = StorePaths(tmp_path / "data")
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def m(uid: str, name: str, value: float, ts: datetime = T0, unit: Unit = Unit.COUNT) -> Metric:
    return Metric(resource_uid=uid, name=name, value=value, unit=unit, ts=ts)


async def test_writes_series_samples_and_events(engine: Engine) -> None:
    w = StoreWriter(engine)
    await w(
        "ha",
        "sensors",
        Normalized(metrics=[m("ha:sensor:a", "value", 1.0), m("ha:sensor:b", "value", 2.0)]),
        [
            Event(
                resource_uid="ha:sensor:a",
                type="state_change",
                severity=Severity.ERROR,
                message="x",
                ts=T0,
            )
        ],
    )
    with engine.connect() as conn:
        series = conn.execute(select(series_t).order_by(series_t.c.id)).all()
        assert [(s.provider, s.resource_uid, s.metric, s.unit) for s in series] == [
            ("ha", "ha:sensor:a", "value", "count"),
            ("ha", "ha:sensor:b", "value", "count"),
        ]
        assert all(s.first_seen == s.last_seen == int(T0.timestamp()) for s in series)
        samples = conn.execute(select(samples_t).order_by(samples_t.c.series_id)).all()
        assert [(s.series_id, s.ts, s.value) for s in samples] == [
            (series[0].id, int(T0.timestamp()), 1.0),
            (series[1].id, int(T0.timestamp()), 2.0),
        ]
        (ev,) = conn.execute(select(events_t)).all()
        assert (ev.resource_uid, ev.type, ev.severity, ev.message, ev.ts) == (
            "ha:sensor:a",
            "state_change",
            "error",
            "x",
            int(T0.timestamp()),
        )
    assert (w.samples_written, w.events_written) == (2, 1)


async def test_repeat_polls_append_samples_and_bump_last_seen(engine: Engine) -> None:
    w = StoreWriter(engine)
    t1 = T0 + timedelta(seconds=30)
    await w("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 1.0)]), [])
    await w("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 3.0, ts=t1)]), [])
    with engine.connect() as conn:
        (s,) = conn.execute(select(series_t)).all()
        assert (s.first_seen, s.last_seen) == (int(T0.timestamp()), int(t1.timestamp()))
        q = select(samples_t.c.ts, samples_t.c.value).order_by(samples_t.c.ts)
        rows = conn.execute(q).all()
        assert [tuple(r) for r in rows] == [(int(T0.timestamp()), 1.0), (int(t1.timestamp()), 3.0)]


async def test_same_second_sample_replaces_rather_than_failing(engine: Engine) -> None:
    w = StoreWriter(engine)
    await w("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 1.0)]), [])
    await w("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 2.0)]), [])
    with engine.connect() as conn:
        rows = conn.execute(select(samples_t.c.value)).all()
        assert [r.value for r in rows] == [2.0]


async def test_empty_result_writes_nothing(engine: Engine) -> None:
    w = StoreWriter(engine)
    await w("ha", "s", Normalized(), [])
    with engine.connect() as conn:
        assert conn.execute(select(series_t)).all() == []
    assert (w.samples_written, w.events_written) == (0, 0)


async def test_unit_change_is_followed_with_a_warning(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    w = StoreWriter(engine)
    await w("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 1.0)]), [])
    w2 = StoreWriter(engine)  # fresh id cache, as after a restart
    with caplog.at_level("WARNING", logger="hud.collector.persist"):
        await w2("ha", "s", Normalized(metrics=[m("ha:sensor:a", "value", 1.0, unit=Unit.PCT)]), [])
    assert "changed unit count → pct" in caplog.text
    with engine.connect() as conn:
        (s,) = conn.execute(select(series_t)).all()
        assert s.unit == "pct"


async def test_concurrent_groups_serialize_through_one_writer(engine: Engine) -> None:
    w = StoreWriter(engine)
    await asyncio.gather(
        *(
            w("p", f"g{i}", Normalized(metrics=[m(f"p:thing:r{i}", "v", float(i))]), [])
            for i in range(20)
        )
    )
    with engine.connect() as conn:
        assert len(conn.execute(select(series_t)).all()) == 20
        assert len(conn.execute(select(samples_t)).all()) == 20
