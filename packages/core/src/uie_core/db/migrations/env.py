"""Alembic env : URL depuis UIE_DATABASE_URL, metadata = uie_core.db.Base."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from uie_core.catalog import records  # noqa: F401  (tables du catalogue réel, V2a)
from uie_core.db.base import Base
from uie_core.delivery import records as delivery_records  # noqa: F401  (envois vers une cible par API)
from uie_core.exports import records as export_records  # noqa: F401  (exports PARTNER)
from uie_core.imports import records as import_records  # noqa: F401  (imports de fichiers, V2c.1)
from uie_core.registry import models  # noqa: F401  (enregistre les tables)
from uie_core.wizard import records as wizard_records  # noqa: F401  (cache des colonnes du Wizard)
from uie_core.wizard import templates  # noqa: F401  (modèles de questionnaire du Wizard)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
if url := os.environ.get("UIE_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
