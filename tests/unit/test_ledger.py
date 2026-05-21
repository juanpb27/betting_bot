"""Tests del ledger de bankroll (TDD).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from betting_bot.bankroll.ledger import BankrollLedger
from tests.factories import build_event, build_pick

VALID_BOOK = "betplay"


def _make_pick(session: Session) -> str:
    """Inserta un Event + Pick mínimos y devuelve el pick.id (para stakes/payouts)."""
    event = build_event()
    session.add(event)
    session.flush()
    pick = build_pick(
        event.id,
        generated_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        generated_date=date(2026, 5, 20),
    )
    session.add(pick)
    session.flush()
    return pick.id


# --- Registro de movimientos --------------------------------------------------


def test_record_deposit_stores_positive_amount(session: Session) -> None:
    ledger = BankrollLedger(session)
    mv = ledger.record_deposit(VALID_BOOK, 750000)
    assert mv.id is not None
    assert mv.book_code == VALID_BOOK
    assert mv.movement_type == "deposit"
    assert mv.amount == 750000


def test_record_withdrawal_stores_negative_amount(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 200000)
    mv = ledger.record_withdrawal(VALID_BOOK, 100000)
    assert mv.movement_type == "withdrawal"
    assert mv.amount == -100000


def test_record_bet_stake_stores_negative_amount_with_pick(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 100000)
    mv = ledger.record_bet_stake(VALID_BOOK, 30000, pick_id)
    assert mv.movement_type == "bet_stake"
    assert mv.amount == -30000
    assert mv.related_pick_id == pick_id


def test_record_bet_payout_stores_positive_amount_with_pick(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    mv = ledger.record_bet_payout(VALID_BOOK, 64500, pick_id)
    assert mv.movement_type == "bet_payout"
    assert mv.amount == 64500
    assert mv.related_pick_id == pick_id


def test_record_adjustment_keeps_sign(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 100000)
    negative = ledger.record_adjustment(VALID_BOOK, -25000, notes="bonus expirado")
    positive = ledger.record_adjustment(VALID_BOOK, 10000, notes="reconciliación")
    assert negative.amount == -25000
    assert negative.notes == "bonus expirado"
    assert positive.amount == 10000


# --- Validaciones de input ----------------------------------------------------


def test_unknown_book_code_raises(session: Session) -> None:
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="book_code"):
        ledger.record_deposit("casa_fantasma", 50000)


@pytest.mark.parametrize("amount", [0, -1, -50000])
def test_non_positive_deposit_raises(session: Session, amount: int) -> None:
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="> 0"):
        ledger.record_deposit(VALID_BOOK, amount)


@pytest.mark.parametrize("amount", [0, -100])
def test_non_positive_withdrawal_raises(session: Session, amount: int) -> None:
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="> 0"):
        ledger.record_withdrawal(VALID_BOOK, amount)


def test_non_positive_stake_raises(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="> 0"):
        ledger.record_bet_stake(VALID_BOOK, 0, pick_id)


def test_non_positive_payout_raises(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="> 0"):
        ledger.record_bet_payout(VALID_BOOK, -10, pick_id)


def test_zero_adjustment_raises(session: Session) -> None:
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="0"):
        ledger.record_adjustment(VALID_BOOK, 0)


# --- Saldo negativo: el ledger lo rechaza -------------------------------------

def test_withdrawal_exceeding_balance_raises(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 50000)
    with pytest.raises(ValueError, match="negativo"):
        ledger.record_withdrawal(VALID_BOOK, 50001)


def test_withdrawal_from_empty_book_raises(session: Session) -> None:
    ledger = BankrollLedger(session)
    with pytest.raises(ValueError, match="negativo"):
        ledger.record_withdrawal(VALID_BOOK, 1000)


def test_withdrawal_to_exact_zero_is_allowed(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 50000)
    ledger.record_withdrawal(VALID_BOOK, 50000)
    assert ledger.get_balance_by_book()[VALID_BOOK] == 0


def test_bet_stake_exceeding_balance_raises(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 20000)
    with pytest.raises(ValueError, match="negativo"):
        ledger.record_bet_stake(VALID_BOOK, 30000, pick_id)


def test_negative_adjustment_exceeding_balance_raises(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit(VALID_BOOK, 10000)
    with pytest.raises(ValueError, match="negativo"):
        ledger.record_adjustment(VALID_BOOK, -25000)


# --- Cálculo de balances ------------------------------------------------------


def test_get_balance_by_book_returns_all_known_books(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 750000)
    balances = ledger.get_balance_by_book()
    assert balances["betplay"] == 750000
    # Las casas sin movimientos aparecen en 0.
    assert balances["codere"] == 0
    assert balances["rushbet"] == 0
    assert balances["bwin"] == 0


def test_balance_reflects_mixed_movements(session: Session) -> None:
    pick_id = _make_pick(session)
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 750000)
    ledger.record_withdrawal("betplay", 100000)
    ledger.record_bet_stake("betplay", 30000, pick_id)
    ledger.record_bet_payout("betplay", 64500, pick_id)
    ledger.record_adjustment("betplay", -500)
    # 750000 - 100000 - 30000 + 64500 - 500
    assert ledger.get_balance_by_book()["betplay"] == 684000


def test_get_total_balance_sums_all_books(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 750000)
    ledger.record_deposit("codere", 625000)
    ledger.record_withdrawal("codere", 25000)
    assert ledger.get_total_balance() == 1350000


def test_get_total_balance_empty_is_zero(session: Session) -> None:
    assert BankrollLedger(session).get_total_balance() == 0
