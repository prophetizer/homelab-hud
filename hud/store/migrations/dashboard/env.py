# SPDX-License-Identifier: Apache-2.0
"""Alembic environment for ``dashboard.db`` (state: users, prefs, audit)."""

from alembic import context

from hud.store.tables import dashboard_metadata

target_metadata = dashboard_metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        msg = "dashboard migrations must be run through hud.store.migrate.upgrade_all"
        raise RuntimeError(msg)
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    msg = "offline mode is not supported"
    raise RuntimeError(msg)
run_migrations_online()
