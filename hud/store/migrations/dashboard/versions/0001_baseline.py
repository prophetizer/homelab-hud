# SPDX-License-Identifier: Apache-2.0
"""dashboard.db baseline (PLAN.md §6.1)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subject", sa.Text, nullable=False, unique=True),
        sa.Column("display_name", sa.Text),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("groups_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("last_seen", sa.Integer),
    )
    op.create_table(
        "user_prefs",
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value_json", sa.Text),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.Integer, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target", sa.Text),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("detail_json", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("user_prefs")
    op.drop_table("users")
