"""wizard templates (questionnaires du Wizard sauvegardés par utilisateur)

Revision ID: 5b2e9c4d1a77
Revises: c74bac7b2242
Create Date: 2026-10-21 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5b2e9c4d1a77"
down_revision = "c74bac7b2242"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wizard_templates",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_wizard_templates_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_wizard_templates_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wizard_templates")),
        sa.UniqueConstraint("organization_id", "user_id", "name", name="uq_wizard_templates_name"),
    )
    op.create_index(
        op.f("ix_wizard_templates_organization_id"), "wizard_templates", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_wizard_templates_user_id"), "wizard_templates", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_wizard_templates_user_id"), table_name="wizard_templates")
    op.drop_index(op.f("ix_wizard_templates_organization_id"), table_name="wizard_templates")
    op.drop_table("wizard_templates")
