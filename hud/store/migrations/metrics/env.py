# SPDX-License-Identifier: Apache-2.0
"""Alembic environment for ``metrics.db`` (series, samples, rollups, availability, events).

The connection arrives with ``schema_translate_map={"metrics": None}`` so the ``metrics.``
prefix used at runtime (ATTACH) is dropped while migrating the file directly.
"""

from alembic import context

from hud.store.tables import metrics_metadata

target_metadata = metrics_metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        msg = "metrics migrations must be run through hud.store.migrate.upgrade_all"
        raise RuntimeError(msg)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    msg = "offline mode is not supported"
    raise RuntimeError(msg)
run_migrations_online()
