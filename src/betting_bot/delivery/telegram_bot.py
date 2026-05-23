"""Capa async de Telegram: fábrica de `Application` + wrappers de comandos.

Cada `cmd_X` async:
1. Verifica autorización contra `settings.telegram_chat_id`.
2. Abre una `Session` corta de SQLAlchemy.
3. Llama al `handle_X` puro de `telegram_handlers.py`.
4. Si `handle_X` levanta `ValueError`, rollbackea y devuelve "ERROR: <msg>".
   Si levanta cualquier otra excepción, rollbackea, loguea el stack con
   `logger.exception` y responde "ERROR interno" (no re-lanza; PTB ya logueará
   excepciones no manejadas, pero acá las atrapamos para no dejar la sesión
   colgada). Migración a `app.add_error_handler` + structlog = Etapa 8.
5. Si todo OK, commitea la sesión.

Nota: usamos MarkdownV2 para responses; los handlers ya escapan sus inputs.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery import telegram_handlers as h
from betting_bot.persistence.repo import PickRepo, SystemStateRepo

_log = logging.getLogger(__name__)


def is_authorized_chat(*, chat_id: int | None, authorized_id: int) -> bool:
    """Único criterio de autorización: chat_id == settings.telegram_chat_id."""
    return chat_id is not None and chat_id == authorized_id


def build_application(
    *, token: str, authorized_chat_id: int, engine: Engine
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Arma la Application con todos los CommandHandler registrados."""
    SessionFactory = sessionmaker(bind=engine)  # noqa: N806 — fábrica, no instancia
    app = Application.builder().token(token).build()

    # Registramos cada comando envolviendo handlers puros + autorización + session.
    for cmd_name, handler_fn in _COMMAND_MAP.items():
        app.add_handler(
            CommandHandler(
                cmd_name,
                _wrap(
                    handler_fn,
                    authorized_chat_id=authorized_chat_id,
                    session_factory=SessionFactory,
                ),
            )
        )
    return app


# Tipo de los wrappers de la capa de delivery: cada uno recibe `args` (lista
# de strings posteriores al comando) y un dict de dependencias inyectadas, y
# devuelve el texto de respuesta.
_Handler = Callable[..., str]


def _wrap(
    handler_fn: _Handler,
    *,
    authorized_chat_id: int,
    session_factory: sessionmaker[Session],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]:
    """Envuelve un `handle_X` puro como un `CommandHandler.callback` async."""

    async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not is_authorized_chat(chat_id=chat_id, authorized_id=authorized_chat_id):
            _log.warning(
                "unauthorized_chat",
                extra={"chat_id": chat_id, "user_id": update.effective_user.id if update.effective_user else None},
            )
            return  # silencio deliberado: no confirmamos existencia al sondeo

        args = context.args or []
        session = session_factory()
        try:
            response = _dispatch(handler_fn, args=args, session=session)
            session.commit()
        except ValueError as e:
            session.rollback()
            response = f"ERROR: {h.escape_md(str(e))}"
        except Exception:
            session.rollback()
            _log.exception("handler_failed", extra={"handler": handler_fn.__name__})
            response = "ERROR interno\\. Ya está logueado\\."
        finally:
            session.close()

        if update.effective_message is not None:
            await update.effective_message.reply_text(
                response, parse_mode=ParseMode.MARKDOWN_V2
            )

    return callback


def _dispatch(handler_fn: _Handler, *, args: list[str], session: Session) -> str:
    """Inyecta las dependencias que cada handler necesita por su firma.

    Mantiene los handlers puros (no se enteran de la session) sin meter un
    framework de DI completo.
    """
    name = handler_fn.__name__
    ledger = BankrollLedger(session)
    system_repo = SystemStateRepo(session)
    pick_repo = PickRepo(session)

    if name == "handle_start":
        return handler_fn()
    if name == "handle_help":
        return handler_fn()
    if name == "handle_status":
        return handler_fn(system_repo=system_repo)
    if name == "handle_balance":
        return handler_fn(ledger=ledger)
    if name == "handle_bankroll":
        return handler_fn(ledger=ledger, pick_repo=pick_repo)
    if name == "handle_deposit":
        return handler_fn(args=args, ledger=ledger)
    if name == "handle_withdraw":
        return handler_fn(args=args, ledger=ledger)
    if name == "handle_adjust":
        return handler_fn(args=args, ledger=ledger)
    if name == "handle_pause":
        return handler_fn(args=args, system_repo=system_repo)
    if name == "handle_resume":
        return handler_fn(system_repo=system_repo)
    raise RuntimeError(f"handler no registrado: {name}")


# Mapeo declarativo de comando → handler puro.
_COMMAND_MAP: dict[str, _Handler] = {
    "start": h.handle_start,
    "help": h.handle_help,
    "status": h.handle_status,
    "balance": h.handle_balance,
    "bankroll": h.handle_bankroll,
    "deposit": h.handle_deposit,
    "withdraw": h.handle_withdraw,
    "adjust": h.handle_adjust,
    "pause": h.handle_pause,
    "resume": h.handle_resume,
}
