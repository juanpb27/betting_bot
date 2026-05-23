"""Wizard inline para confirmar / descartar un pick desde Telegram.

Estructura:
1. **Pure stuff** (parsers, validators, keyboards, operaciones atómicas) —
   testeable sin python-telegram-bot. Es el grueso de la lógica.
2. **Async wiring** — `ConversationHandler` de PTB con 5 estados
   (CHOOSING_BOOK, ENTERING_PRICE, ENTERING_STAKE, CONFIRMING,
   CHOOSING_REASON), `conversation_timeout=600s`, fallback `/cancel`.
   Llama a las funciones puras.

Flujo del wizard:
- Notificación de pick llega con 3 botones inline (built by pick_notifier).
- User aprieta "Ya apostada" → wizard pregunta casa, cuota, stake, confirma.
- User aprieta "Descartar" → wizard pregunta motivo (4 predefinidos).
- User aprieta "Ver detalles" → respuesta placeholder (TBD Etapa 7).

Atomicidad: `atomic_place_pick` y `atomic_skip_pick` ejecutan
`mark_placed`/`mark_skipped` + escritura al ledger + enqueue Sheets EN LA
MISMA sesión. El caller (la capa async) envuelve todo en `session_scope`
que commitea al salir bien o rollbackea ante excepción.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery.pick_notifier import parse_pick_action
from betting_bot.delivery.telegram_handlers import escape_md, fmt_amount
from betting_bot.logging_setup import bind_request_id, get_logger
from betting_bot.persistence.models import Pick
from betting_bot.persistence.repo import PendingSheetsSyncRepo, PickRepo
from betting_bot.yaml_config import load_book_codes, load_destination_books

_log = get_logger(__name__)

# Prefijos cortitos para callback_data del wizard (cabe en 64 bytes).
_CB_WZ = "wz"
_CB_BOOK = f"{_CB_WZ}:book"
_CB_REASON = f"{_CB_WZ}:reason"

# Estados del ConversationHandler. Enteros porque PTB los compara con ==.
(
    CHOOSING_BOOK,
    ENTERING_PRICE,
    ENTERING_STAKE,
    CONFIRMING,
    CHOOSING_REASON,
) = range(5)

# Motivos predefinidos del descarte. Clave = slug (callback_data); valor =
# texto humano que va al botón y a `pick.skip_reason`.
SKIP_REASONS: dict[str, str] = {
    "low_odds": "Cuota local insuficiente",
    "no_balance": "Sin saldo en casa",
    "not_convinced": "No me convence",
    "other": "Otro",
}

# Timeout del wizard: 10 min sin actividad → expira y libera estado.
WIZARD_TIMEOUT_SECONDS = 600


# --- Parsers ---------------------------------------------------------------


def parse_book_callback(callback_data: str) -> str:
    """Extrae el `book_code` de `wz:book:<code>`."""
    parts = callback_data.split(":")
    if len(parts) != 3 or f"{parts[0]}:{parts[1]}" != _CB_BOOK or not parts[2]:
        raise ValueError(f"callback_data de book inválido: {callback_data!r}")
    return parts[2]


def parse_reason_callback(callback_data: str) -> str:
    """Extrae el slug del motivo de `wz:reason:<slug>`."""
    parts = callback_data.split(":")
    if len(parts) != 3 or f"{parts[0]}:{parts[1]}" != _CB_REASON or not parts[2]:
        raise ValueError(f"callback_data de reason inválido: {callback_data!r}")
    return parts[2]


# --- Validators ------------------------------------------------------------


def validate_price(text: str) -> float:
    """Cuota decimal > 1.0. Acepta `2.10` o `2,30` (separador latam)."""
    if not text:
        raise ValueError("cuota vacía")
    normalized = text.replace(",", ".").strip()
    try:
        value = float(normalized)
    except ValueError as exc:
        raise ValueError(f"cuota inválida: {text!r}") from exc
    if value <= 1.0:
        raise ValueError(f"cuota debe ser > 1.0 (recibida {value})")
    return value


_STAKE_RE = re.compile(r"^\d+(?:[.,]\d{3})*$")


def validate_stake(text: str, *, max_amount: int) -> int:
    """Stake entero positivo. Acepta separadores de miles latam: `25.000` o
    `25,000` → `25000`. Rechaza decimales reales (`10.5` tiene un solo
    dígito post-separador, NO es un agrupado de miles). Sin signo
    explícito (uso `validate_stake` solo para apuestas, siempre positivas)."""
    if not text:
        raise ValueError("stake vacío")
    cleaned = text.replace(" ", "").strip()
    if not _STAKE_RE.match(cleaned):
        raise ValueError(f"stake inválido: {text!r}")
    value = int(cleaned.replace(".", "").replace(",", ""))
    if value <= 0:
        raise ValueError(f"stake debe ser > 0 (recibido {value})")
    if value > max_amount:
        raise ValueError(
            f"stake {value} excede saldo disponible ({max_amount})"
        )
    return value


# --- Keyboards -------------------------------------------------------------


def build_book_keyboard() -> InlineKeyboardMarkup:
    """Una fila de botones, uno por casa destino habilitada."""
    books = load_destination_books()
    if not books:
        raise ValueError("No hay destination_books habilitadas en books.yaml")
    # Dos columnas para que entre prolijo en pantalla.
    buttons = [
        InlineKeyboardButton(b.name, callback_data=f"{_CB_BOOK}:{b.code}")
        for b in books
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def build_skip_reason_keyboard() -> InlineKeyboardMarkup:
    """Botones predefinidos para razones de descarte."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{_CB_REASON}:{slug}")
        for slug, label in SKIP_REASONS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


# --- Operaciones atómicas --------------------------------------------------


def atomic_place_pick(
    *,
    session: Session,
    pick_id: str,
    actual_book: str,
    actual_price: float,
    actual_stake: int,
    source: str,
) -> Pick:
    """`mark_placed` + `record_bet_stake` + 2 enqueues (pick + movement)
    todo en la misma sesión. Si cualquiera falla, el caller hace rollback
    del scope y se revierte todo. NO commitea — el caller es responsable.
    """
    pick_repo = PickRepo(session)
    ledger = BankrollLedger(session)
    queue = PendingSheetsSyncRepo(session)

    pick = pick_repo.mark_placed(
        pick_id,
        actual_book=actual_book,
        actual_price=actual_price,
        actual_stake=actual_stake,
    )
    mv = ledger.record_bet_stake(
        book_code=actual_book,
        amount=actual_stake,
        related_pick_id=pick_id,
        notes=f"wizard:{source}",
    )
    queue.enqueue("pick", {"pick_id": pick_id})
    queue.enqueue("movement", {"movement_id": mv.id, "source": source})
    return pick


def atomic_skip_pick(
    *, session: Session, pick_id: str, reason: str
) -> Pick:
    """`mark_skipped` + enqueue pick a Sheets. No toca el ledger (no se
    apostó)."""
    pick = PickRepo(session).mark_skipped(pick_id, reason=reason)
    PendingSheetsSyncRepo(session).enqueue("pick", {"pick_id": pick_id})
    return pick


# --- Wiring async (ConversationHandler) -----------------------------------


def build_conversation_handler(
    *, session_factory: sessionmaker[Session]
) -> ConversationHandler[Any]:
    """Arma el ConversationHandler que maneja todo el flujo del wizard.

    Entry: CallbackQueryHandler matcheando los callbacks `pa:*` del
    `pick_notifier`. Si la acción es `placed` o `skip`, entra a la
    conversación; si es `details`, responde placeholder y termina.

    States:
      CHOOSING_BOOK   → user eligió "Ya apostada", esperamos botón de casa
      ENTERING_PRICE  → esperamos texto con la cuota
      ENTERING_STAKE  → esperamos texto con el stake
      CONFIRMING      → esperamos confirmación final (botón sí/no)
      CHOOSING_REASON → user eligió "Descartar", esperamos botón de motivo
    """
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(_entry_pick_action, pattern=r"^pa:"),
        ],
        states={
            CHOOSING_BOOK: [
                CallbackQueryHandler(
                    _wrap_state(_handle_chose_book, session_factory),
                    pattern=rf"^{_CB_BOOK}:",
                ),
            ],
            ENTERING_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    _handle_entered_price,
                ),
            ],
            ENTERING_STAKE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    _wrap_state(_handle_entered_stake, session_factory),
                ),
            ],
            CONFIRMING: [
                CallbackQueryHandler(
                    _wrap_state(_handle_confirmed, session_factory),
                    pattern=r"^wz:confirm:",
                ),
            ],
            CHOOSING_REASON: [
                CallbackQueryHandler(
                    _wrap_state(_handle_chose_reason, session_factory),
                    pattern=rf"^{_CB_REASON}:",
                ),
            ],
            # `ConversationHandler.TIMEOUT` es un estado especial: si el
            # conversation_timeout expira sin actividad, PTB enruta aquí
            # antes de cerrar la conversación. Notificamos al usuario para
            # que sepa que el wizard se cerró y que el pick sigue pendiente
            # (puede retomar apretando "Ya apostada" en la notificación).
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _handle_timeout),
                CallbackQueryHandler(_handle_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", _handle_cancel),
        ],
        conversation_timeout=WIZARD_TIMEOUT_SECONDS,
        per_user=True,
        per_chat=True,
        # PTB v21: si nos falta este flag, los CallbackQueryHandler internos
        # no matchean en estados. `per_message=False` es el default OK
        # cuando mezclamos message + callback en distintos estados.
    )


async def _entry_pick_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Punto de entrada: parsea `pa:<action>:<pick_id>` y enruta al estado."""
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    try:
        action, pick_id = parse_pick_action(query.data)
    except ValueError:
        _log.warning("wizard_bad_entry_callback", data=query.data)
        return ConversationHandler.END

    context.user_data["pick_id"] = pick_id  # type: ignore[index]

    if action == "placed":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(  # type: ignore[union-attr]
            "¿En qué casa apostaste?", reply_markup=build_book_keyboard()
        )
        return CHOOSING_BOOK
    if action == "skip":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(  # type: ignore[union-attr]
            "¿Motivo del descarte?", reply_markup=build_skip_reason_keyboard()
        )
        return CHOOSING_REASON
    if action == "details":
        await query.message.reply_text(  # type: ignore[union-attr]
            "Detalles disponibles en próximas etapas\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END
    return ConversationHandler.END


def _wrap_state(
    handler: Any,
    session_factory: sessionmaker[Session],
) -> Any:
    """Wrapper que inyecta `session_factory` y `bind_request_id` en cada
    state handler que necesite tocar DB."""

    async def wrapped(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        with bind_request_id():
            result: int = await handler(update, context, session_factory)
            return result

    return wrapped


async def _handle_chose_book(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],  # noqa: ARG001 — no toca DB aún
) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    try:
        book_code = parse_book_callback(query.data)
    except ValueError as exc:
        await query.message.reply_text(f"Error: {exc}")  # type: ignore[union-attr]
        return ConversationHandler.END
    # Validar contra books.yaml (defensa extra; los botones ya vienen de ahí).
    if book_code not in load_book_codes():
        await query.message.reply_text(  # type: ignore[union-attr]
            f"Casa desconocida: {book_code}"
        )
        return ConversationHandler.END
    context.user_data["book"] = book_code  # type: ignore[index]
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(  # type: ignore[union-attr]
        f"Casa: {book_code}\\.\n¿Qué cuota conseguiste? \\(ej: `2.15`\\)",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ENTERING_PRICE


async def _handle_entered_price(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    msg = update.effective_message
    if msg is None or msg.text is None:
        return ENTERING_PRICE
    try:
        price = validate_price(msg.text)
    except ValueError as exc:
        await msg.reply_text(f"ERROR: {escape_md(str(exc))}\\. Intentá de nuevo o /cancel\\.",
                             parse_mode=ParseMode.MARKDOWN_V2)
        return ENTERING_PRICE
    context.user_data["price"] = price  # type: ignore[index]
    await msg.reply_text("¿Stake apostado? \\(entero, ej: `25000`\\)",
                         parse_mode=ParseMode.MARKDOWN_V2)
    return ENTERING_STAKE


async def _handle_entered_stake(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
) -> int:
    msg = update.effective_message
    if msg is None or msg.text is None:
        return ENTERING_STAKE
    book = context.user_data.get("book")  # type: ignore[union-attr]
    if not isinstance(book, str):
        await msg.reply_text("Estado del wizard perdido. Mandá el comando otra vez.")
        return ConversationHandler.END
    # Validar stake contra el saldo actual del libro.
    with session_factory() as session:
        balances = BankrollLedger(session).get_balance_by_book()
    max_amount = balances.get(book, 0)
    try:
        stake = validate_stake(msg.text, max_amount=max_amount)
    except ValueError as exc:
        await msg.reply_text(
            f"ERROR: {escape_md(str(exc))}\\. Intentá de nuevo o /cancel\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ENTERING_STAKE
    context.user_data["stake"] = stake  # type: ignore[index]
    # Pedir confirmación final.
    price = context.user_data.get("price")  # type: ignore[union-attr]
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirmar", callback_data="wz:confirm:yes"),
                InlineKeyboardButton("Cancelar", callback_data="wz:confirm:no"),
            ]
        ]
    )
    await msg.reply_text(
        f"Confirmá: apostaste {fmt_amount(stake)} en `{escape_md(book)}` "
        f"@ `{price}`",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return CONFIRMING


async def _handle_confirmed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    confirmed = query.data.endswith(":yes")
    if not confirmed:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Cancelado\\.", parse_mode=ParseMode.MARKDOWN_V2)  # type: ignore[union-attr]
        return ConversationHandler.END

    pick_id = context.user_data.get("pick_id")  # type: ignore[union-attr]
    book = context.user_data.get("book")  # type: ignore[union-attr]
    price = context.user_data.get("price")  # type: ignore[union-attr]
    stake = context.user_data.get("stake")  # type: ignore[union-attr]
    # Tipos exactos: `bool` es subclase de `int`, NO lo aceptamos por accidente.
    if (
        not isinstance(pick_id, str)
        or not isinstance(book, str)
        or not isinstance(price, float)
        or type(stake) is not int
    ):
        await query.message.reply_text("Estado del wizard incompleto\\.", parse_mode=ParseMode.MARKDOWN_V2)  # type: ignore[union-attr]
        return ConversationHandler.END

    try:
        with session_factory() as session:
            atomic_place_pick(
                session=session,
                pick_id=pick_id,
                actual_book=book,
                actual_price=price,
                actual_stake=stake,
                source="wizard",
            )
            session.commit()
    except ValueError as exc:
        _log.warning("wizard_place_rejected", error=str(exc))
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(  # type: ignore[union-attr]
            f"No se pudo registrar: {escape_md(str(exc))}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(  # type: ignore[union-attr]
        f"Registrado: {fmt_amount(stake)} en `{escape_md(book)}` @ `{price}`",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def _handle_chose_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    try:
        slug = parse_reason_callback(query.data)
    except ValueError:
        return ConversationHandler.END
    reason = SKIP_REASONS.get(slug, slug)
    pick_id = context.user_data.get("pick_id")  # type: ignore[union-attr]
    if not isinstance(pick_id, str):
        return ConversationHandler.END
    try:
        with session_factory() as session:
            atomic_skip_pick(session=session, pick_id=pick_id, reason=reason)
            session.commit()
    except ValueError as exc:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(  # type: ignore[union-attr]
            f"No se pudo descartar: {escape_md(str(exc))}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(  # type: ignore[union-attr]
        f"Descartado: {escape_md(reason)}",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def _handle_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE  # noqa: ARG001
) -> int:
    msg = update.effective_message
    if msg is not None:
        await msg.reply_text("Wizard cancelado\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END


async def _handle_timeout(
    update: Update, context: ContextTypes.DEFAULT_TYPE  # noqa: ARG001
) -> int:
    """Notifica al usuario que el wizard expiró por inactividad. El pick
    sigue como `pending` en DB — el user puede retomar apretando los botones
    de la notificación original (los botones siguen activos hasta que el
    user los apriete, en cuyo caso se desactivan al iniciar el wizard)."""
    _log.info("wizard_timeout_expired")
    chat = update.effective_chat
    if chat is not None and update.get_bot() is not None:
        try:
            await update.get_bot().send_message(
                chat_id=chat.id,
                text="Wizard expirado por inactividad\\. "
                "El pick sigue pendiente — apretá los botones de la "
                "notificación original para retomar\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception:
            _log.exception("wizard_timeout_notify_failed")
    return ConversationHandler.END
