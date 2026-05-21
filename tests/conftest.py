from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from betting_bot.persistence.db import apply_sqlite_pragmas
from betting_bot.persistence.models import Base


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Engine SQLite en memoria con el schema creado.
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    apply_sqlite_pragmas(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Sesión sobre la DB en memoria del test."""
    factory = sessionmaker(bind=engine)
    sess = factory()
    yield sess
    sess.close()
