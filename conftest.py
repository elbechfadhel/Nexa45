"""Fixtures partagées (Core, connecteurs, API) : PostgreSQL de test (UIE_TEST_DATABASE_URL) via Alembic."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_URL = os.environ.get("UIE_TEST_DATABASE_URL", "postgresql+psycopg://uie:uie@localhost:5433/uie_test")
CORE_DIR = Path(__file__).resolve().parent / "packages" / "core"


def _ensure_database() -> None:
    admin = TEST_URL.rsplit("/", 1)[0] + "/postgres"
    dbname = TEST_URL.rsplit("/", 1)[1]
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        exists = c.execute(text("SELECT 1 FROM pg_database WHERE datname=:n"), {"n": dbname}).scalar()
        if not exists:
            c.execute(text(f'CREATE DATABASE "{dbname}"'))
    eng.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_database()
    env = {**os.environ, "UIE_DATABASE_URL": TEST_URL}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=CORE_DIR,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=CORE_DIR,
        env=env,
        check=True,
        capture_output=True,
    )
    eng = create_engine(TEST_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """Chaque test tourne dans une transaction annulée à la fin."""
    conn = engine.connect()
    trans = conn.begin()
    s = sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")()
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()
