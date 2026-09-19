# SPDX-License-Identifier: Apache-2.0
"""SQLite engine construction (PLAN.md §6).

One connection per request/task, opened on ``dashboard.db`` with ``metrics.db`` attached
as schema ``metrics``. Pragmas are applied on every new DBAPI connection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event

from hud.store.tables import METRICS_SCHEMA

BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class StorePaths:
    data_dir: Path

    @property
    def dashboard_db(self) -> Path:
        return self.data_dir / "dashboard.db"

    @property
    def metrics_db(self) -> Path:
        return self.data_dir / "metrics.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def all_db_files(self) -> list[Path]:
        return [self.dashboard_db, self.metrics_db]

    def size_bytes(self) -> int:
        """On-disk footprint including WAL/SHM side files, for ``/api/v1/health``."""
        total = 0
        for db in self.all_db_files():
            for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
                if p.exists():
                    total += p.stat().st_size
        return total


def _apply_pragmas(conn: sqlite3.Connection, schema: str | None = None) -> None:
    prefix = f"{schema}." if schema else ""
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA {prefix}journal_mode = WAL")
        cur.execute(f"PRAGMA {prefix}synchronous = NORMAL")
    finally:
        cur.close()


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def create_file_engine(path: Path) -> Engine:
    """Engine bound to a single file, no ATTACH. Used for migrations and backups."""
    engine = create_engine(_sqlite_url(path))

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: sqlite3.Connection, _record: Any) -> None:  # noqa: ANN401
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()
        _apply_pragmas(dbapi_conn)

    return engine


def create_store_engine(paths: StorePaths) -> Engine:
    """Runtime engine: ``dashboard.db`` with ``metrics.db`` attached as ``metrics``."""
    engine = create_engine(_sqlite_url(paths.dashboard_db))
    metrics_path = str(paths.metrics_db)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: sqlite3.Connection, _record: Any) -> None:  # noqa: ANN401
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute(f"ATTACH DATABASE ? AS {METRICS_SCHEMA}", (metrics_path,))
        cur.close()
        _apply_pragmas(dbapi_conn)
        _apply_pragmas(dbapi_conn, METRICS_SCHEMA)

    return engine
