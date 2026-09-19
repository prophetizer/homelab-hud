# SPDX-License-Identifier: Apache-2.0
"""Apply Alembic baselines/upgrades to both database files at startup (PLAN.md §11.3).

Each file has its own migration environment and ``alembic_version`` table, so deleting
``metrics.db`` (the documented recovery for a corrupt/oversized history) recreates it
from its baseline on the next start without touching ``dashboard.db``.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

from hud.store.engine import StorePaths, create_file_engine
from hud.store.tables import METRICS_SCHEMA

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# (environment name, path property on StorePaths, schema_translate_map for that file)
_ENVIRONMENTS: tuple[tuple[str, str, dict[str, None] | None], ...] = (
    ("dashboard", "dashboard_db", None),
    ("metrics", "metrics_db", {METRICS_SCHEMA: None}),
)


def alembic_config(env: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR / env))
    return cfg


def _current_and_head(connection: Connection, cfg: Config) -> tuple[str | None, str]:
    ctx = MigrationContext.configure(connection)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        msg = f"expected exactly one head, found {heads}"
        raise RuntimeError(msg)
    return ctx.get_current_revision(), heads[0]


def _backup(path: Path, backups_dir: Path) -> Path:
    """WAL-safe copy via the sqlite3 online backup API."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir / f"{path.stem}-premigrate-{stamp}{path.suffix}"
    with sqlite3.connect(path) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    return dest


def _prepare_new_file(path: Path) -> None:
    """``auto_vacuum`` can only be chosen before the first table exists (§6.2 relies on it)."""
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")


def upgrade_all(paths: StorePaths) -> dict[str, str]:
    """Bring every database file to head. Returns ``{env: head_revision}``."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    for env, attr, translate in _ENVIRONMENTS:
        path: Path = getattr(paths, attr)
        is_new = not path.exists() or path.stat().st_size == 0
        if is_new:
            _prepare_new_file(path)
        engine: Engine = create_file_engine(path)
        cfg = alembic_config(env)
        try:
            with engine.connect() as raw_conn:
                conn = (
                    raw_conn.execution_options(schema_translate_map=translate)
                    if translate
                    else raw_conn
                )
                current, head = _current_and_head(conn, cfg)
                if current == head:
                    log.info("%s at head %s", path.name, head)
                    result[env] = head
                    continue
                if not is_new and current is not None:
                    dest = _backup(path, paths.backups_dir)
                    log.warning(
                        "%s: migrating %s -> %s, backup at %s", path.name, current, head, dest
                    )
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "head")
                conn.commit()
                result[env] = head
        finally:
            engine.dispose()
    return result
