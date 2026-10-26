"""correspondances de valeurs d'une source fichier (mémoire du dossier)

Une source FICHIER (journal de factures) n'a pas d'instance : ses correspondances catégorie → compte et taux →
clé de taxe sont mémorisées pour le dossier (mandant, `client_key`) de l'organisation et présélectionnées au
fichier suivant. `instance_id` devient facultatif ; unicité de la portée fichier par un index partiel.

Revision ID: 6e1f0a2b9c47
Revises: 52d0a2c091c4
Create Date: 2026-10-26 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "6e1f0a2b9c47"
down_revision = "52d0a2c091c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("export_value_mappings", sa.Column("client_key", sa.String(length=60), nullable=True))
    op.alter_column(
        "export_value_mappings", "instance_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    op.create_index(
        "uq_export_value_mappings_file_scope",
        "export_value_mappings",
        ["organization_id", "target_system", "target_product", "client_key", "kind", "source_ref"],
        unique=True,
        postgresql_where=sa.text("instance_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_export_value_mappings_file_scope",
        table_name="export_value_mappings",
        postgresql_where=sa.text("instance_id IS NULL"),
    )
    op.execute("DELETE FROM export_value_mappings WHERE instance_id IS NULL")
    op.alter_column(
        "export_value_mappings", "instance_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    op.drop_column("export_value_mappings", "client_key")
