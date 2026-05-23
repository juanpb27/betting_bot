"""Tests del middleware de autorización + wrapper async del bot.

Regla: solo `settings.telegram_chat_id` puede mandar comandos. Cualquier
otro chat se rechaza sin responder (silencioso) y se loguea como warning.
El silencio es deliberado: no querés que un bot sondeado por terceros les
confirme que existe.

También testeamos el wrapper async (`_wrap`) que aplica autorización,
maneja la sesión SQLAlchemy (commit/rollback) y captura excepciones. Es
crítico porque un error en el flujo de transacciones puede dejar el ledger
en estado inconsistente.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from betting_bot.delivery import telegram_handlers as h
from betting_bot.delivery.telegram_bot import _wrap, is_authorized_chat
from betting_bot.persistence.db import apply_sqlite_pragmas
from betting_bot.persistence.models import BankrollMovement, Base


def test_is_authorized_chat_accepts_configured_id() -> None:
    assert is_authorized_chat(chat_id=12345, authorized_id=12345) is True


def test_is_authorized_chat_rejects_other_ids() -> None:
    assert is_authorized_chat(chat_id=99999, authorized_id=12345) is False


def test_is_authorized_chat_rejects_none() -> None:
    # Update sin chat_id (raro pero defensivo).
    assert is_authorized_chat(chat_id=None, authorized_id=12345) is False


def test_build_application_registers_all_commands() -> None:
    """Smoke: la Application se construye y los 10 comandos quedan registrados."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from betting_bot.delivery.telegram_bot import build_application
    from betting_bot.persistence.db import apply_sqlite_pragmas
    from betting_bot.persistence.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    apply_sqlite_pragmas(engine)
    Base.metadata.create_all(engine)

    app = build_application(
        token="123:fake-token-shape", authorized_chat_id=42, engine=engine
    )
    registered: set[str] = set()
    for group in app.handlers.values():
        for handler in group:
            cmds = getattr(handler, "commands", None)  # frozenset[str] | None
            if cmds:
                registered.update(cmds)
    expected = {
        "start", "help", "status", "balance", "bankroll",
        "deposit", "withdraw", "adjust", "pause", "resume",
    }
    assert expected.issubset(registered)


# --- Wrapper async ----------------------------------------------------------

AUTHORIZED = 42


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    apply_sqlite_pragmas(eng)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Any]:
    return sessionmaker(bind=engine)


def _build_update(*, chat_id: int | None) -> MagicMock:
    """Mock mínimo de `telegram.Update` con chat/usuario/message reply async."""
    update = MagicMock()
    if chat_id is None:
        update.effective_chat = None
    else:
        update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=1)
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def _build_context(args: list[str] | None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args
    return ctx


async def test_wrap_rejects_unauthorized_chat_silently(
    session_factory: sessionmaker[Any],
) -> None:
    callback = _wrap(
        h.handle_help, authorized_chat_id=AUTHORIZED, session_factory=session_factory
    )
    update = _build_update(chat_id=999)
    await callback(update, _build_context([]))
    # Silencio total: no se le respondió nada al chat no autorizado.
    update.effective_message.reply_text.assert_not_called()


async def test_wrap_commits_on_success(
    session_factory: sessionmaker[Any], engine: Engine
) -> None:
    callback = _wrap(
        h.handle_deposit,
        authorized_chat_id=AUTHORIZED,
        session_factory=session_factory,
    )
    update = _build_update(chat_id=AUTHORIZED)
    await callback(update, _build_context(["betplay", "150000"]))
    update.effective_message.reply_text.assert_awaited_once()
    # El movimiento quedó persistido (commit OK).
    with sessionmaker(bind=engine)() as s:
        total = s.execute(
            select(BankrollMovement).where(BankrollMovement.book_code == "betplay")
        ).scalar_one()
        assert total.amount == 150_000


async def test_wrap_rollbacks_and_replies_on_value_error(
    session_factory: sessionmaker[Any], engine: Engine
) -> None:
    callback = _wrap(
        h.handle_deposit,
        authorized_chat_id=AUTHORIZED,
        session_factory=session_factory,
    )
    update = _build_update(chat_id=AUTHORIZED)
    # `foo` no es book válido → ValueError dentro del handler.
    await callback(update, _build_context(["foo", "100000"]))
    # Respondió al usuario con el error.
    update.effective_message.reply_text.assert_awaited_once()
    response = update.effective_message.reply_text.call_args[0][0]
    assert "ERROR" in response
    # Rollback: no quedó ningún movimiento.
    with sessionmaker(bind=engine)() as s:
        rows = s.execute(select(BankrollMovement)).all()
        assert rows == []
