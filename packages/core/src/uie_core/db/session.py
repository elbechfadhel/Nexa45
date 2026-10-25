"""Engine et sessions SQLAlchemy.

L'URL vient de UIE_DATABASE_URL (ex : postgresql+psycopg://uie:uie@localhost:5433/uie).
Un fichier .env (cherché depuis le répertoire courant vers le haut) est chargé à l'import ;
les variables déjà définies dans l'environnement restent prioritaires.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv(find_dotenv(usecwd=True), override=False)

DEFAULT_URL = "postgresql+psycopg://uie:uie@localhost:5433/uie"


def database_url() -> str:
    return os.environ.get("UIE_DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=8)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=8)
def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transaction courte : commit si OK, rollback sinon."""
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
