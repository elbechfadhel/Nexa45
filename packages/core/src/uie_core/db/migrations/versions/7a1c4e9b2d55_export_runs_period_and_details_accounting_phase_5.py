"""export_runs : period et details (ACCOUNTING phase 5)

Revision ID: 7a1c4e9b2d55
Revises: 3f292744a370
Create Date: 2026-10-06 09:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7a1c4e9b2d55"
down_revision = "3f292744a370"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("export_runs", sa.Column("period", sa.String(length=7), nullable=True))
    op.add_column("export_runs", sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index(op.f("ix_export_runs_period"), "export_runs", ["period"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_export_runs_period"), table_name="export_runs")
    op.drop_column("export_runs", "details")
    op.drop_column("export_runs", "period")
