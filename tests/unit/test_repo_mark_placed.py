"""TDD de `PickRepo.mark_placed` y `mark_skipped` (Etapa 6).

Reglas de negocio:
- `mark_placed` setea status='placed', placed_at, actual_book, actual_price,
  actual_stake. Rechaza si status != 'pending' (cierra el race de doble-click
  en el wizard inline).
- `mark_skipped` setea status='skipped', skip_reason. Rechaza si status !=
  'pending'.
- Ambos NO escriben a `bankroll_movements` — eso es responsabilidad del caller
  (el wizard hace `mark_placed` + `record_bet_stake` en la misma sesión, atómico
  por el commit del scope).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from betting_bot.persistence.repo import PickRepo
from tests.factories import build_event, build_pick


def _create_pending_pick(session: Session) -> str:
    event = build_event()
    session.add(event)
    session.flush()
    repo = PickRepo(session)
    pick = build_pick(event_id=event.id)
    created, _ = repo.create(pick, generated_at=datetime.now(UTC))
    return created.id


def test_get_with_event_returns_both_when_exists(session: Session) -> None:
    # Helper para el worker / notifier: en una sola query devuelve pick + event,
    # evita N+1 en el render de la fila Sheets / mensaje Telegram.
    event = build_event(home_team="Arsenal", away_team="Chelsea")
    session.add(event)
    session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(session).create(pick, generated_at=datetime.now(UTC))

    result = PickRepo(session).get_with_event(pick.id)
    assert result is not None
    got_pick, got_event = result
    assert got_pick.id == pick.id
    assert got_event.id == event.id
    assert got_event.home_team == "Arsenal"


def test_get_with_event_returns_none_for_unknown(session: Session) -> None:
    assert PickRepo(session).get_with_event("nonexistent") is None


# --- mark_placed ------------------------------------------------------------


def test_mark_placed_sets_status_and_actual_fields(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    placed = repo.mark_placed(
        pick_id,
        actual_book="betplay",
        actual_price=2.15,
        actual_stake=25_000,
    )
    assert placed.status == "placed"
    assert placed.actual_book == "betplay"
    assert placed.actual_price == 2.15
    assert placed.actual_stake == 25_000
    assert placed.placed_at is not None


def test_mark_placed_is_readable_after_flush(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    PickRepo(session).mark_placed(
        pick_id, actual_book="codere", actual_price=2.30, actual_stake=10_000
    )
    # Mismo session: identity map garantiza ver el cambio sin commit/reload.
    fresh = PickRepo(session).get(pick_id)
    assert fresh is not None
    assert fresh.status == "placed"


def test_mark_placed_with_explicit_placed_at(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    when = datetime(2026, 5, 24, 19, 30, tzinfo=UTC)
    placed = PickRepo(session).mark_placed(
        pick_id,
        actual_book="betplay",
        actual_price=2.10,
        actual_stake=15_000,
        placed_at=when,
    )
    # SQLite no preserva tzinfo en TIMESTAMP; comparamos por epoch.
    assert placed.placed_at is not None
    assert placed.placed_at.replace(tzinfo=UTC) == when


def test_mark_placed_rejects_if_already_placed(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    repo.mark_placed(
        pick_id, actual_book="betplay", actual_price=2.15, actual_stake=25_000
    )
    # Segundo mark sobre el mismo pick: debe rechazar (doble-click en wizard).
    # Match laxo — no acoplar al wording exacto del mensaje, solo a que mencione
    # 'pending' que es la palabra clave del contrato.
    with pytest.raises(ValueError, match="pending"):
        repo.mark_placed(
            pick_id, actual_book="codere", actual_price=2.30, actual_stake=10_000
        )


def test_mark_placed_rejects_if_already_skipped(session: Session) -> None:
    # Otro path para no-pending: pick skipped no puede ser placed después.
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    repo.mark_skipped(pick_id, reason="cambié de opinión")
    with pytest.raises(ValueError, match="pending"):
        repo.mark_placed(
            pick_id, actual_book="betplay", actual_price=2.0, actual_stake=10_000
        )


def test_mark_placed_rejects_for_unknown_pick(session: Session) -> None:
    repo = PickRepo(session)
    with pytest.raises(ValueError, match="no encontrado"):
        repo.mark_placed(
            "nonexistent-id",
            actual_book="betplay",
            actual_price=2.0,
            actual_stake=10_000,
        )


def test_mark_placed_rejects_invalid_price_or_stake(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    with pytest.raises(ValueError, match="cuota"):
        repo.mark_placed(
            pick_id, actual_book="betplay", actual_price=1.0, actual_stake=10_000
        )
    with pytest.raises(ValueError, match="stake"):
        repo.mark_placed(
            pick_id, actual_book="betplay", actual_price=2.0, actual_stake=0
        )


# --- mark_skipped -----------------------------------------------------------


def test_mark_skipped_sets_status_and_reason(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    skipped = repo.mark_skipped(pick_id, reason="cuota local insuficiente")
    assert skipped.status == "skipped"
    assert skipped.skip_reason == "cuota local insuficiente"
    # No se setea placed_at en skipped.
    assert skipped.placed_at is None


def test_mark_skipped_rejects_if_already_skipped(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    repo.mark_skipped(pick_id, reason="no me convence")
    with pytest.raises(ValueError, match="pending"):
        repo.mark_skipped(pick_id, reason="otro motivo")


def test_mark_skipped_rejects_if_already_placed(session: Session) -> None:
    # Otro path para no-pending: pick placed no puede ser skipped después.
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    repo.mark_placed(
        pick_id, actual_book="betplay", actual_price=2.0, actual_stake=10_000
    )
    with pytest.raises(ValueError, match="pending"):
        repo.mark_skipped(pick_id, reason="cambié de opinión")


def test_mark_skipped_rejects_for_unknown_pick(session: Session) -> None:
    repo = PickRepo(session)
    with pytest.raises(ValueError, match="no encontrado"):
        repo.mark_skipped("nonexistent-id", reason="x")


def test_mark_skipped_rejects_empty_reason(session: Session) -> None:
    pick_id = _create_pending_pick(session)
    repo = PickRepo(session)
    with pytest.raises(ValueError, match="reason"):
        repo.mark_skipped(pick_id, reason="")
    with pytest.raises(ValueError, match="reason"):
        repo.mark_skipped(pick_id, reason="   ")


# --- Race condition de doble-click -----------------------------------------


def test_mark_placed_race_two_sessions_first_wins(engine: Engine) -> None:
    """Reproduce la race real: dos sesiones independientes leen el pick como
    pending y ambas llaman mark_placed. UPDATE condicional asegura que solo la
    primera matchea filas; la segunda obtiene rowcount==0 y rechaza.

    Sin el UPDATE condicional (versión vieja con check Python-side), ambas
    pasaban y la segunda escritura ganaba, dejando el wizard del primer click
    con un bet_stake huérfano contra un pick que muestra datos del segundo.
    """
    SessionFactory = sessionmaker(bind=engine)  # noqa: N806
    # Setup: crear pick pending en una sesión, commitear.
    setup_session = SessionFactory()
    event = build_event()
    setup_session.add(event)
    setup_session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(setup_session).create(pick, generated_at=datetime.now(UTC))
    pick_id = pick.id
    setup_session.commit()
    setup_session.close()

    # Dos sesiones concurrentes intentan mark_placed.
    s1 = SessionFactory()
    s2 = SessionFactory()
    PickRepo(s1).mark_placed(
        pick_id, actual_book="betplay", actual_price=2.20, actual_stake=20_000
    )
    s1.commit()
    # Segunda sesión: la fila ya no está pending; rechaza.
    with pytest.raises(ValueError, match="pending"):
        PickRepo(s2).mark_placed(
            pick_id, actual_book="codere", actual_price=2.30, actual_stake=30_000
        )
    s1.close()
    s2.close()

    # Verificación: ganó la primera sesión (betplay, no codere).
    verify = SessionFactory()
    placed = PickRepo(verify).get(pick_id)
    assert placed is not None
    assert placed.actual_book == "betplay"
    assert placed.actual_stake == 20_000
    verify.close()


def test_mark_skipped_race_two_sessions_first_wins(engine: Engine) -> None:
    """Mismo patrón que mark_placed pero para skipped."""
    SessionFactory = sessionmaker(bind=engine)  # noqa: N806
    setup_session = SessionFactory()
    event = build_event()
    setup_session.add(event)
    setup_session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(setup_session).create(pick, generated_at=datetime.now(UTC))
    pick_id = pick.id
    setup_session.commit()
    setup_session.close()

    s1 = SessionFactory()
    s2 = SessionFactory()
    PickRepo(s1).mark_skipped(pick_id, reason="cuota insuficiente")
    s1.commit()
    with pytest.raises(ValueError, match="pending"):
        PickRepo(s2).mark_skipped(pick_id, reason="no me convence")
    s1.close()
    s2.close()
