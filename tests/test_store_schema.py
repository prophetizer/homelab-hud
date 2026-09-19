# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from hud.store import StorePaths, create_store_engine, upgrade_all
from hud.store.tables import availability, dashboard_metadata, metrics_metadata, users

METRICS_TABLES = {"series", "samples", "rollups", "availability", "events"}
DASHBOARD_TABLES = {"users", "user_prefs", "audit_log"}


@pytest.fixture
def paths(tmp_path: Path) -> StorePaths:
    return StorePaths(data_dir=tmp_path / "data")


@pytest.fixture
def engine(paths: StorePaths) -> Engine:
    upgrade_all(paths)
    eng = create_store_engine(paths)
    yield eng
    eng.dispose()


def test_upgrade_creates_both_files_at_head(paths: StorePaths) -> None:
    result = upgrade_all(paths)
    assert result == {"dashboard": "0001_baseline", "metrics": "0001_baseline"}
    assert paths.dashboard_db.exists()
    assert paths.metrics_db.exists()
    assert not paths.backups_dir.exists(), "fresh files must not trigger a pre-migration backup"


def test_tables_land_in_the_right_file(engine: Engine) -> None:
    insp = inspect(engine)
    assert set(insp.get_table_names()) == DASHBOARD_TABLES | {"alembic_version"}
    assert set(insp.get_table_names(schema="metrics")) == METRICS_TABLES | {"alembic_version"}


def test_metadata_matches_migrations(engine: Engine) -> None:
    """tables.py must describe exactly what the baseline created (columns + names)."""
    insp = inspect(engine)
    for md in (dashboard_metadata, metrics_metadata):
        for table in md.tables.values():
            cols = {c["name"] for c in insp.get_columns(table.name, schema=table.schema)}
            assert cols == {c.name for c in table.columns}, table.name


def test_pragmas_applied(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA metrics.journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
        assert conn.execute(text("PRAGMA metrics.synchronous")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA metrics.auto_vacuum")).scalar() == 2  # INCREMENTAL


def test_without_rowid_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        sql = {
            row[0]: row[1]
            for row in conn.execute(
                text("SELECT name, sql FROM metrics.sqlite_master WHERE type='table'")
            )
        }
    assert "WITHOUT ROWID" in sql["samples"]
    assert "WITHOUT ROWID" in sql["rollups"]
    assert "WITHOUT ROWID" not in sql["series"]


def test_partial_unique_index_allows_one_open_span_per_resource(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(insert(availability).values(resource_uid="r1", state="up", started_at=100))
        # a second resource may also be open
        conn.execute(insert(availability).values(resource_uid="r2", state="up", started_at=100))
        # a closed span for r1 does not collide with its open one
        conn.execute(
            insert(availability).values(resource_uid="r1", state="down", started_at=0, ended_at=100)
        )
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(insert(availability).values(resource_uid="r1", state="down", started_at=200))


def test_uptime_query_from_plan_6_3(engine: Engine) -> None:
    """Window 1000..2000, now=2000. r1: up 0..1500, down 1500..(open). Expect 0.5."""
    with engine.begin() as conn:
        conn.execute(
            insert(availability).values(
                [
                    {"resource_uid": "r1", "state": "up", "started_at": 0, "ended_at": 1500},
                    {"resource_uid": "r1", "state": "down", "started_at": 1500, "ended_at": None},
                    # r2 up the whole time, open span
                    {"resource_uid": "r2", "state": "up", "started_at": 500, "ended_at": None},
                ]
            )
        )
        rows = conn.execute(
            text(
                """
                SELECT resource_uid,
                       SUM(MIN(COALESCE(ended_at, :now), :to) - MAX(started_at, :from))
                         FILTER (WHERE state = 'up') * 1.0
                       / (:to - :from) AS uptime_ratio
                FROM metrics.availability
                WHERE started_at < :to AND COALESCE(ended_at, :now) > :from
                GROUP BY resource_uid
                ORDER BY resource_uid
                """
            ),
            {"now": 2000, "from": 1000, "to": 2000},
        ).all()
    assert [(r[0], r[1]) for r in rows] == [("r1", 0.5), ("r2", 1.0)]


def test_deleting_metrics_db_recreates_it_without_touching_dashboard(paths: StorePaths) -> None:
    upgrade_all(paths)
    eng = create_store_engine(paths)
    with eng.begin() as conn:
        conn.execute(insert(users).values(subject="alice", source="local", created_at=1))
    eng.dispose()

    paths.metrics_db.unlink()
    upgrade_all(paths)

    eng = create_store_engine(paths)
    with eng.connect() as conn:
        assert conn.execute(select(users.c.subject)).scalar() == "alice"
        assert set(inspect(eng).get_table_names(schema="metrics")) >= METRICS_TABLES
    eng.dispose()
    assert not paths.backups_dir.exists()


def test_idempotent_upgrade_and_size(paths: StorePaths) -> None:
    upgrade_all(paths)
    upgrade_all(paths)
    assert paths.size_bytes() > 0
