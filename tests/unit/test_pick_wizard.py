"""TDD del wizard inline de pick (Etapa 6 — paso 8).

Hay tres familias de cosas que se testean acá:

1. **Parsers/validators puros** (sin tocar DB): `parse_book_callback`,
   `parse_reason_callback`, `validate_price`, `validate_stake`.
2. **Keyboards** (no tocan Telegram en runtime, solo construyen
   `InlineKeyboardMarkup` desde config): `build_book_keyboard`,
   `build_skip_reason_keyboard`.
3. **Operaciones atómicas** (sí tocan DB): `atomic_place_pick` y
   `atomic_skip_pick`. Estas son CRÍTICAS porque tocan el ledger; el wizard
   las llama dentro del `session_scope` para que el commit final sea atómico
   con `mark_placed + record_bet_stake + enqueue Sheets`.

El wiring async del `ConversationHandler` (paso 9) NO se testea acá — solo
estas piezas puras que son testables sin python-telegram-bot mocks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery.pick_wizard import (
    SKIP_REASONS,
    atomic_place_pick,
    atomic_skip_pick,
    build_book_keyboard,
    build_skip_reason_keyboard,
    parse_book_callback,
    parse_reason_callback,
    validate_price,
    validate_stake,
)
from betting_bot.persistence.models import BankrollMovement, PendingSheetsSync
from betting_bot.persistence.repo import PickRepo
from tests.factories import build_event, build_pick


def _make_pending_pick(session: Session) -> str:
    event = build_event()
    session.add(event)
    session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(session).create(pick, generated_at=datetime.now(UTC))
    return pick.id


# --- Parsers ---------------------------------------------------------------


def test_parse_book_callback_extracts_book_code() -> None:
    assert parse_book_callback("wz:book:betplay") == "betplay"
    assert parse_book_callback("wz:book:codere") == "codere"


def test_parse_book_callback_rejects_bad_format() -> None:
    for bad in ["wz:book:", "wz:book", "xx:book:betplay", "wz:wrong:betplay"]:
        with pytest.raises(ValueError):
            parse_book_callback(bad)


def test_parse_reason_callback_extracts_slug() -> None:
    assert parse_reason_callback("wz:reason:low_odds") == "low_odds"


def test_parse_reason_callback_rejects_bad_format() -> None:
    with pytest.raises(ValueError):
        parse_reason_callback("wz:reason:")
    with pytest.raises(ValueError):
        parse_reason_callback("xx:reason:foo")


# --- Validators ------------------------------------------------------------


def test_validate_price_accepts_decimal_above_1() -> None:
    assert validate_price("2.10") == 2.10
    assert validate_price("1.01") == 1.01
    # Comma → punto (UX latam).
    assert validate_price("2,30") == 2.30


def test_validate_price_rejects_non_numeric_or_le_1() -> None:
    for bad in ["abc", "", "0.99", "1.0", "1", "-2.0"]:
        with pytest.raises(ValueError):
            validate_price(bad)


def test_validate_stake_accepts_positive_int() -> None:
    assert validate_stake("25000", max_amount=1_000_000) == 25_000
    # Separadores comunes en UX latam.
    assert validate_stake("25.000", max_amount=1_000_000) == 25_000
    assert validate_stake("25,000", max_amount=1_000_000) == 25_000


def test_validate_stake_rejects_non_int_or_le_0() -> None:
    for bad in ["abc", "", "-1000", "0", "10.5", "25000.5"]:
        with pytest.raises(ValueError):
            validate_stake(bad, max_amount=1_000_000)


def test_validate_stake_rejects_above_max() -> None:
    with pytest.raises(ValueError, match="excede"):
        validate_stake("2000000", max_amount=1_000_000)


# --- Keyboards -------------------------------------------------------------


def test_book_keyboard_has_button_per_destination_book() -> None:
    kb = build_book_keyboard()
    flat = [btn for row in kb.inline_keyboard for btn in row]
    # books.yaml hoy tiene 4 destination_books habilitados.
    assert len(flat) >= 4
    # Cada botón con callback parseable.
    for btn in flat:
        code = parse_book_callback(btn.callback_data)
        assert code  # no vacío
        assert len(btn.callback_data.encode("utf-8")) <= 64


def test_skip_reason_keyboard_has_predefined_reasons() -> None:
    kb = build_skip_reason_keyboard()
    flat = [btn for row in kb.inline_keyboard for btn in row]
    assert len(flat) == len(SKIP_REASONS)
    slugs = {parse_reason_callback(btn.callback_data) for btn in flat}
    assert slugs == set(SKIP_REASONS.keys())


# --- atomic_place_pick -----------------------------------------------------


def test_atomic_place_pick_marks_placed_records_stake_and_enqueues(
    session: Session,
) -> None:
    pick_id = _make_pending_pick(session)
    # Necesita saldo en la casa para que record_bet_stake no rechace.
    BankrollLedger(session).record_deposit("betplay", 500_000)

    atomic_place_pick(
        session=session,
        pick_id=pick_id,
        actual_book="betplay",
        actual_price=2.20,
        actual_stake=25_000,
        source="wizard",
    )

    # Pick pasó a placed con todos los campos actual_*.
    pick = PickRepo(session).get(pick_id)
    assert pick is not None
    assert pick.status == "placed"
    assert pick.actual_book == "betplay"
    assert pick.actual_price == 2.20
    assert pick.actual_stake == 25_000
    # Movement bet_stake con related_pick_id correcto.
    mv = session.execute(
        select(BankrollMovement).where(
            BankrollMovement.movement_type == "bet_stake",
            BankrollMovement.related_pick_id == pick_id,
        )
    ).scalar_one()
    assert mv.amount == -25_000  # negativo = sale plata
    assert mv.book_code == "betplay"
    # 2 enqueues a sheets sync: pick + movement.
    rows = session.execute(select(PendingSheetsSync)).scalars().all()
    types = sorted(r.payload_type for r in rows)
    assert types == ["movement", "pick"]


def test_atomic_place_pick_rejects_if_pick_already_placed(
    session: Session,
) -> None:
    pick_id = _make_pending_pick(session)
    BankrollLedger(session).record_deposit("betplay", 500_000)
    # Pre-place manual.
    PickRepo(session).mark_placed(
        pick_id, actual_book="betplay", actual_price=2.0, actual_stake=10_000
    )

    with pytest.raises(ValueError, match="pending"):
        atomic_place_pick(
            session=session,
            pick_id=pick_id,
            actual_book="codere",
            actual_price=2.20,
            actual_stake=25_000,
            source="wizard",
        )


def test_atomic_place_pick_overdraw_rolls_back_mark_placed(
    engine: Engine,
) -> None:
    """Si record_bet_stake levanta (saldo negativo), el mark_placed previo
    debe rollbackearse cuando el caller (session_scope) haga rollback.

    Usamos DOS sesiones: una para setup (commitea event + pick + deposit) y
    otra para el wizard (que rollbackea ante la excepción). Sin esto, el
    rollback de la sesión "wizard" arrastra al setup también.
    """
    SessionFactory = sessionmaker(bind=engine)  # noqa: N806

    setup = SessionFactory()
    pick_id = _make_pending_pick(setup)
    BankrollLedger(setup).record_deposit("betplay", 5_000)  # saldo bajo
    setup.commit()
    setup.close()

    wizard = SessionFactory()
    with pytest.raises(ValueError, match="negativo"):
        atomic_place_pick(
            session=wizard,
            pick_id=pick_id,
            actual_book="betplay",
            actual_price=2.20,
            actual_stake=25_000,
            source="wizard",
        )
    wizard.rollback()
    wizard.close()

    # Verificación en sesión fresca: el pick NO quedó placed.
    verify = SessionFactory()
    pick = PickRepo(verify).get(pick_id)
    assert pick is not None
    assert pick.status == "pending"
    verify.close()


# --- atomic_skip_pick ------------------------------------------------------


def test_atomic_skip_pick_marks_skipped_and_enqueues(session: Session) -> None:
    pick_id = _make_pending_pick(session)
    atomic_skip_pick(session=session, pick_id=pick_id, reason="cuota local insuficiente")
    pick = PickRepo(session).get(pick_id)
    assert pick is not None
    assert pick.status == "skipped"
    assert pick.skip_reason == "cuota local insuficiente"
    # Solo 1 enqueue (pick, no hay movement porque no se apostó).
    rows = session.execute(select(PendingSheetsSync)).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload_type == "pick"


def test_atomic_skip_pick_rejects_if_already_skipped(session: Session) -> None:
    pick_id = _make_pending_pick(session)
    PickRepo(session).mark_skipped(pick_id, reason="x")
    with pytest.raises(ValueError, match="pending"):
        atomic_skip_pick(session=session, pick_id=pick_id, reason="otro")


# --- Entry async handler (smoke con mocks de PTB) -------------------------


def _make_update_with_callback(callback_data: str) -> Any:
    """Mock mínimo de `Update` con un `callback_query` y un `effective_message`
    que pretende ser editable + responde async."""
    from unittest.mock import AsyncMock, MagicMock

    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = callback_data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


async def test_entry_routes_placed_to_choosing_book_state() -> None:
    from betting_bot.delivery.pick_wizard import (
        CHOOSING_BOOK,
        _entry_pick_action,
    )
    update = _make_update_with_callback("pa:placed:uuid-1")
    context = type("C", (), {"user_data": {}})()
    state = await _entry_pick_action(update, context)
    assert state == CHOOSING_BOOK
    assert context.user_data["pick_id"] == "uuid-1"
    # Tras parsear, se llamó a edit + reply (con keyboard).
    update.callback_query.edit_message_reply_markup.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once()


async def test_entry_routes_skip_to_choosing_reason_state() -> None:
    from betting_bot.delivery.pick_wizard import (
        CHOOSING_REASON,
        _entry_pick_action,
    )
    update = _make_update_with_callback("pa:skip:uuid-2")
    context = type("C", (), {"user_data": {}})()
    state = await _entry_pick_action(update, context)
    assert state == CHOOSING_REASON
    assert context.user_data["pick_id"] == "uuid-2"


async def test_entry_details_action_responds_placeholder_and_ends() -> None:
    from telegram.ext import ConversationHandler

    from betting_bot.delivery.pick_wizard import _entry_pick_action
    update = _make_update_with_callback("pa:details:uuid-3")
    context = type("C", (), {"user_data": {}})()
    state = await _entry_pick_action(update, context)
    assert state == ConversationHandler.END
    update.callback_query.message.reply_text.assert_awaited_once()


async def test_entry_bad_callback_ends_silently() -> None:
    from telegram.ext import ConversationHandler

    from betting_bot.delivery.pick_wizard import _entry_pick_action
    update = _make_update_with_callback("garbage")
    context = type("C", (), {"user_data": {}})()
    state = await _entry_pick_action(update, context)
    assert state == ConversationHandler.END
    # No respondió al user (callback inválido → silencioso, no confirma).
    update.callback_query.message.reply_text.assert_not_called()
