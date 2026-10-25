"""v2c2b not_relevant appris par systeme

Revision ID: 42b6c816119f
Revises: 8c3d5f3e3355
Create Date: 2026-09-30 16:56:14.287283
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "42b6c816119f"
down_revision = "8c3d5f3e3355"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_imports", sa.Column("resolved_ignored", sa.Integer(), nullable=True))
    # lignes V2c.2 existantes : des mappings (server_default retiré ensuite : le modèle fixe la valeur)
    op.add_column(
        "learned_column_mappings",
        sa.Column("decision", sa.String(length=20), nullable=False, server_default="MAPPED"),
    )
    op.alter_column("learned_column_mappings", "decision", server_default=None)
    op.add_column("learned_column_mappings", sa.Column("system_id", sa.UUID(), nullable=True))
    op.alter_column(
        "learned_column_mappings", "semantic_key", existing_type=sa.VARCHAR(length=100), nullable=True
    )
    op.drop_constraint(
        op.f("uq_learned_column_mappings_organization_id_header_key"),
        "learned_column_mappings",
        type_="unique",
    )
    op.create_index(
        op.f("ix_learned_column_mappings_system_id"), "learned_column_mappings", ["system_id"], unique=False
    )
    op.create_index(
        "uq_learned_column_mappings_mapped",
        "learned_column_mappings",
        ["organization_id", "header_key"],
        unique=True,
        postgresql_where=sa.text("system_id IS NULL"),
    )
    op.create_index(
        "uq_learned_column_mappings_not_relevant",
        "learned_column_mappings",
        ["organization_id", "header_key", "system_id"],
        unique=True,
        postgresql_where=sa.text("system_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_learned_column_mappings_system_id_systems"),
        "learned_column_mappings",
        "systems",
        ["system_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # non détecté par l'autogénération
    op.create_check_constraint(
        op.f("ck_learned_column_mappings_decision_shape"),
        "learned_column_mappings",
        "(decision = 'MAPPED' AND semantic_key IS NOT NULL AND system_id IS NULL)"
        " OR (decision = 'NOT_RELEVANT' AND semantic_key IS NULL AND system_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_learned_column_mappings_decision_shape"), "learned_column_mappings", type_="check"
    )
    op.execute("DELETE FROM learned_column_mappings WHERE decision = 'NOT_RELEVANT'")
    op.drop_constraint(
        op.f("fk_learned_column_mappings_system_id_systems"), "learned_column_mappings", type_="foreignkey"
    )
    op.drop_index("uq_learned_column_mappings_not_relevant", table_name="learned_column_mappings")
    op.drop_index("uq_learned_column_mappings_mapped", table_name="learned_column_mappings")
    op.drop_index(op.f("ix_learned_column_mappings_system_id"), table_name="learned_column_mappings")
    op.create_unique_constraint(
        op.f("uq_learned_column_mappings_organization_id_header_key"),
        "learned_column_mappings",
        ["organization_id", "header_key"],
    )
    op.alter_column(
        "learned_column_mappings", "semantic_key", existing_type=sa.VARCHAR(length=100), nullable=False
    )
    op.drop_column("learned_column_mappings", "system_id")
    op.drop_column("learned_column_mappings", "decision")
    op.drop_column("catalog_imports", "resolved_ignored")
