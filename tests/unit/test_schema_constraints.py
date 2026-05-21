"""Tests de las garantías a nivel de schema: índices únicos, FKs, CHECKs.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from betting_bot.persistence.models import BankrollMovement, Event, OddsSnapshot, SystemState
from tests.factories import build_event, build_pick

_GEN_AT = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
_GEN_DATE = date(2026, 5, 20)


def _add_event_with_pick(session: Session, *, line: float | None) -> Event:
    event = build_event()
    session.add(event)
    session.flush()
    session.add(
        build_pick(event.id, line=line, generated_at=_GEN_AT, generated_date=_GEN_DATE)
    )
    session.flush()
    return event


# --- Índices únicos parciales: idempotencia diaria de picks -------------------


def test_unique_index_rejects_duplicate_pick_with_line(session: Session) -> None:
    event = _add_event_with_pick(session, line=2.5)
    session.add(
        build_pick(event.id, line=2.5, generated_at=_GEN_AT, generated_date=_GEN_DATE)
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unique_index_rejects_duplicate_pick_null_line(session: Session) -> None:
    event = _add_event_with_pick(session, line=None)
    session.add(
        build_pick(event.id, line=None, generated_at=_GEN_AT, generated_date=_GEN_DATE)
    )
    with pytest.raises(IntegrityError):
        session.flush()


# --- Foreign keys: el PRAGMA foreign_keys=ON realmente enforcea ---------------


def test_foreign_key_rejects_orphan_movement(session: Session) -> None:
    session.add(
        BankrollMovement(
            book_code="betplay",
            movement_type="adjustment",
            amount=1000,
            related_pick_id="pick-inexistente",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_foreign_key_rejects_orphan_odds_snapshot(session: Session) -> None:
    session.add(
        OddsSnapshot(
            event_id="event-inexistente",
            bookmaker_key="pinnacle",
            market_key="h2h",
            outcome="home",
            price=2.0,
            captured_at=_GEN_AT,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


# --- CHECK constraints --------------------------------------------------------


def test_check_rejects_invalid_movement_type(session: Session) -> None:
    session.add(BankrollMovement(book_code="betplay", movement_type="transfer", amount=1000))
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_rejects_invalid_pick_status(session: Session) -> None:
    event = build_event()
    session.add(event)
    session.flush()
    session.add(
        build_pick(event.id, status="INVALID", generated_at=_GEN_AT, generated_date=_GEN_DATE)
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_rejects_non_singleton_system_state(session: Session) -> None:
    session.add(SystemState(id=2, is_paused=False))
    with pytest.raises(IntegrityError):
        session.flush()
