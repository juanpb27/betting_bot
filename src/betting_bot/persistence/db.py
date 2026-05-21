from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from betting_bot.config import get_settings

_SQLITE_PREFIX = "sqlite:///"


def resolve_database_url() -> str:
    """Devuelve `DATABASE_URL` con el path de SQLite resuelto a absoluto.
    """
    url = get_settings().database_url
    if not url.startswith(_SQLITE_PREFIX):
        return url
    rel = url[len(_SQLITE_PREFIX) :]
    if rel in ("", ":memory:"):
        return url
    path = Path(rel)
    if not path.is_absolute():
        path = get_settings().data_dir.parent / path
    return f"{_SQLITE_PREFIX}{path}"


def apply_sqlite_pragmas(engine: Engine) -> None:
    """Registra un listener que aplica los PRAGMAs de SQLite en cada conexión.

    - `journal_mode=WAL`: lecturas concurrentes con el writer.
    - `foreign_keys=ON`: SQLite NO enforcea FKs por defecto; hay que pedirlo
      explícitamente en cada conexión.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(
        dbapi_connection: DBAPIConnection, _connection_record: ConnectionPoolEntry
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine de producción (singleton, lazy). Importar este módulo no lo crea."""
    engine = create_engine(resolve_database_url())
    apply_sqlite_pragmas(engine)
    return engine


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional: commitea al salir bien, rollback ante excepción."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
