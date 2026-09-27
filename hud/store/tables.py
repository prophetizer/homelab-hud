# SPDX-License-Identifier: Apache-2.0
"""SQLAlchemy Core table definitions (PLAN.md §6.1).

Two ``MetaData`` objects, one per file. ``metrics_metadata`` carries ``schema="metrics"``
because at runtime ``metrics.db`` is ``ATTACH``-ed under that name to the ``dashboard.db``
connection; migrations run directly against the file with the schema translated away.

These are the *current* shapes. Schema changes go through Alembic revisions, never here alone.
"""

from sqlalchemy import (
    REAL,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)

METRICS_SCHEMA = "metrics"

dashboard_metadata = MetaData()
metrics_metadata = MetaData(schema=METRICS_SCHEMA)

# ---------------------------------------------------------------- metrics.db (disposable)

series = Table(
    "series",
    metrics_metadata,
    Column("id", Integer, primary_key=True),
    Column("provider", Text, nullable=False),
    Column("resource_uid", Text, nullable=False),
    Column("metric", Text, nullable=False),
    Column("unit", Text, nullable=False),
    Column("labels_json", Text, nullable=False, server_default="{}"),
    Column("first_seen", Integer, nullable=False),
    Column("last_seen", Integer, nullable=False),
    UniqueConstraint("resource_uid", "metric"),
)

samples = Table(
    "samples",
    metrics_metadata,
    Column("series_id", Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
    Column("ts", Integer, nullable=False),  # unix seconds
    Column("value", REAL, nullable=False),
    PrimaryKeyConstraint("series_id", "ts"),
    sqlite_with_rowid=False,
)

rollups = Table(
    "rollups",
    metrics_metadata,
    Column("series_id", Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
    Column("tier", Text, nullable=False),  # '5m' | '1h' | '1d'
    Column("bucket", Integer, nullable=False),  # unix seconds, bucket start
    Column("min_v", REAL, nullable=False),
    Column("max_v", REAL, nullable=False),
    Column("avg_v", REAL, nullable=False),
    Column("last_v", REAL, nullable=False),
    Column("n", Integer, nullable=False),
    PrimaryKeyConstraint("series_id", "tier", "bucket"),
    sqlite_with_rowid=False,
)

# Availability as transition spans, not samples. Exactly one open span per resource.
availability = Table(
    "availability",
    metrics_metadata,
    Column("id", Integer, primary_key=True),
    Column("resource_uid", Text, nullable=False),
    Column("state", Text, nullable=False),  # up | down | degraded | unknown
    Column("started_at", Integer, nullable=False),
    Column("ended_at", Integer),  # NULL = current span
    Column("reason", Text),
    # Last time the recorder saw this span's state still holding (Phase 2 slice 1).
    Column("confirmed_at", Integer),
    # 1 when rebuilt from logged events rather than observed.
    Column("approximate", Integer, nullable=False, server_default="0"),
)
Index("ix_avail_res_time", availability.c.resource_uid, availability.c.started_at.desc())
Index(
    "ux_avail_open",
    availability.c.resource_uid,
    unique=True,
    sqlite_where=availability.c.ended_at.is_(None),
)

events = Table(
    "events",
    metrics_metadata,
    Column("id", Integer, primary_key=True),
    Column("resource_uid", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("ts", Integer, nullable=False),
)

# ---------------------------------------------------------------- dashboard.db (state)

users = Table(
    "users",
    dashboard_metadata,
    Column("id", Integer, primary_key=True),
    Column("subject", Text, nullable=False, unique=True),  # OIDC sub or local username
    Column("display_name", Text),
    Column("source", Text, nullable=False),  # 'forward' | 'oidc' | 'local'
    Column("password_hash", Text),
    Column("groups_json", Text, nullable=False, server_default="[]"),
    Column("created_at", Integer, nullable=False),
    Column("last_seen", Integer),
)

user_prefs = Table(
    "user_prefs",
    dashboard_metadata,
    Column("user_id", Integer, nullable=False),
    Column("key", Text, nullable=False),
    Column("value_json", Text),
    PrimaryKeyConstraint("user_id", "key"),
)

sessions = Table(
    "sessions",
    dashboard_metadata,
    # Only a SHA-256 of the cookie value is stored: reading the database must not yield a
    # usable session.
    Column("token_hash", Text, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", Text, nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("last_seen", Integer, nullable=False),
    Index("ix_sessions_user", "user_id"),
    Index("ix_sessions_expires", "expires_at"),
)

audit_log = Table(
    "audit_log",
    dashboard_metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Integer, nullable=False),
    Column("subject", Text),
    Column("action", Text, nullable=False),
    Column("target", Text),
    Column("result", Text, nullable=False),
    Column("detail_json", Text),
)
