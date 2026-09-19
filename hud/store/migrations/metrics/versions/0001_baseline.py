# SPDX-License-Identifier: Apache-2.0
"""metrics.db baseline (PLAN.md §6.1)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from hud.store.tables import METRICS_SCHEMA as S

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Series identity, one row per (resource, metric). Keeps sample rows narrow.
    op.create_table(
        "series",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("resource_uid", sa.Text, nullable=False),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("labels_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("first_seen", sa.Integer, nullable=False),
        sa.Column("last_seen", sa.Integer, nullable=False),
        sa.UniqueConstraint("resource_uid", "metric"),
        schema=S,
    )
    # Raw samples. Short retention by design.
    op.create_table(
        "samples",
        sa.Column(
            "series_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.Integer, nullable=False),
        sa.Column("value", sa.REAL, nullable=False),
        sa.PrimaryKeyConstraint("series_id", "ts"),
        schema=S,
        sqlite_with_rowid=False,
    )
    # Tiered rollups. Same shape at every tier.
    op.create_table(
        "rollups",
        sa.Column(
            "series_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("bucket", sa.Integer, nullable=False),
        sa.Column("min_v", sa.REAL, nullable=False),
        sa.Column("max_v", sa.REAL, nullable=False),
        sa.Column("avg_v", sa.REAL, nullable=False),
        sa.Column("last_v", sa.REAL, nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("series_id", "tier", "bucket"),
        schema=S,
        sqlite_with_rowid=False,
    )
    # Availability as transitions, not samples. Exact uptime math, negligible storage.
    op.create_table(
        "availability",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resource_uid", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("started_at", sa.Integer, nullable=False),
        sa.Column("ended_at", sa.Integer),
        sa.Column("reason", sa.Text),
        schema=S,
    )
    op.create_index(
        "ix_avail_res_time",
        "availability",
        ["resource_uid", sa.text("started_at DESC")],
        schema=S,
    )
    # Exactly one open span per resource — the uptime math in §6.3 depends on this.
    op.create_index(
        "ux_avail_open",
        "availability",
        ["resource_uid"],
        unique=True,
        schema=S,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resource_uid", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("ts", sa.Integer, nullable=False),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("events", schema=S)
    op.drop_index("ux_avail_open", "availability", schema=S)
    op.drop_index("ix_avail_res_time", "availability", schema=S)
    op.drop_table("availability", schema=S)
    op.drop_table("rollups", schema=S)
    op.drop_table("samples", schema=S)
    op.drop_table("series", schema=S)
