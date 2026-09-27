# SPDX-License-Identifier: Apache-2.0
"""metrics.db: availability spans learn when they were last confirmed (PLAN.md §12,
Phase 2 slice 1)

``confirmed_at`` is the last time the recorder saw the open span's state still holding: on
restart a dangling span is closed there, and the gap until HUD observes again is "not
observed", not downtime. ``approximate`` marks spans rebuilt from logged events.

Revision ID: 0002_availability_confirmation
Revises: 0001_baseline
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_availability_confirmation"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# No schema= here: the migration runs on metrics.db itself, and unlike create_table (whose
# Table object the connection's schema_translate_map rewrites), add_column emits the
# prefix verbatim — "metrics.availability" does not exist in that file.


def upgrade() -> None:
    op.add_column("availability", sa.Column("confirmed_at", sa.Integer))
    op.add_column(
        "availability",
        sa.Column("approximate", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("availability", "approximate")
    op.drop_column("availability", "confirmed_at")
