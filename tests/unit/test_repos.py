"""Tests de los repositorios de persistencia."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from betting_bot.persistence.models import (
    ApiQuotaLog,
    Event,
    OddsSnapshot,
    Pick,
    SystemState,
)
from betting_bot.persistence.repo import (
    EventRepo,
    OddsRepo,
    PickRepo,
    QuotaRepo,
    SystemStateRepo,
)
from tests.factories import build_event, build_pick


def _add_event(session: Session, odds_api_id: str | None = None) -> Event:
    event = build_event(odds_api_id=odds_api_id)
    session.add(event)
    session.flush()
    return event


def _build_pick(
    event_id: str,
    market_key: str = "h2h",
    outcome: str = "home",
    line: float | None = None,
) -> Pick:
    """Pick con campos requeridos en valores dummy. `generated_*` los setea el repo."""
    return build_pick(event_id, market_key=market_key, outcome=outcome, line=line)


# --- EventRepo ----------------------------------------------------------------


def test_event_repo_add_assigns_uuid(session: Session) -> None:
    repo = EventRepo(session)
    event = repo.add(
        Event(
            league_key="soccer_epl",
            home_team="A",
            away_team="B",
            commence_time=datetime(2026, 5, 25, 19, 0, tzinfo=UTC),
            status="scheduled",
        )
    )
    assert event.id  # UUID v4 asignado por el default del modelo


def test_event_repo_get(session: Session) -> None:
    event = _add_event(session)
    assert EventRepo(session).get(event.id) is event


def test_event_repo_get_missing_returns_none(session: Session) -> None:
    assert EventRepo(session).get("no-existe") is None


def test_event_repo_get_by_odds_api_id(session: Session) -> None:
    event = _add_event(session, odds_api_id="odds-123")
    assert EventRepo(session).get_by_odds_api_id("odds-123") is event
    assert EventRepo(session).get_by_odds_api_id("otro") is None


# --- OddsRepo -----------------------------------------------------------------


def test_odds_repo_add_and_list_ordered_desc(session: Session) -> None:
    event = _add_event(session)
    repo = OddsRepo(session)
    repo.add(
        OddsSnapshot(
            event_id=event.id,
            bookmaker_key="pinnacle",
            market_key="h2h",
            outcome="home",
            price=2.0,
            captured_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        )
    )
    repo.add(
        OddsSnapshot(
            event_id=event.id,
            bookmaker_key="bet365",
            market_key="h2h",
            outcome="home",
            price=2.1,
            captured_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        )
    )
    snapshots = repo.list_for_event(event.id)
    assert len(snapshots) == 2
    # Orden descendente por captured_at: el más reciente primero.
    assert snapshots[0].bookmaker_key == "bet365"


def test_odds_repo_add_many(session: Session) -> None:
    event = _add_event(session)
    repo = OddsRepo(session)
    repo.add_many(
        [
            OddsSnapshot(
                event_id=event.id,
                bookmaker_key=book,
                market_key="h2h",
                outcome="home",
                price=2.0,
                captured_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
            )
            for book in ("pinnacle", "bet365", "betsson")
        ]
    )
    assert len(repo.list_for_event(event.id)) == 3


# --- PickRepo -----------------------------------------------------------------


def test_pick_repo_create_sets_generated_fields(session: Session) -> None:
    event = _add_event(session)
    at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    pick, is_new = PickRepo(session).create(_build_pick(event.id), generated_at=at)
    assert is_new is True
    assert pick.generated_at == at
    assert pick.generated_date == date(2026, 5, 20)


def test_pick_repo_generated_date_uses_project_timezone(session: Session) -> None:
    event = _add_event(session)
    # 02:00 UTC del 21 = 21:00 del 20 en America/Bogota (UTC-5).
    at = datetime(2026, 5, 21, 2, 0, tzinfo=UTC)
    pick, _ = PickRepo(session).create(_build_pick(event.id), generated_at=at)
    assert pick.generated_date == date(2026, 5, 20)


def test_pick_repo_create_is_idempotent_same_day(session: Session) -> None:
    event = _add_event(session)
    repo = PickRepo(session)
    at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    first, first_new = repo.create(_build_pick(event.id, "h2h", "home", None), generated_at=at)
    second, second_new = repo.create(_build_pick(event.id, "h2h", "home", None), generated_at=at)
    assert first_new is True
    assert second_new is False  # contrato del tuple
    assert first.id == second.id
    count = session.execute(select(func.count()).select_from(Pick)).scalar()
    assert count == 1


def test_pick_repo_idempotency_handles_null_line(session: Session) -> None:
    event = _add_event(session)
    repo = PickRepo(session)
    at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    a, _ = repo.create(_build_pick(event.id, "h2h", "home", None), generated_at=at)
    b, _ = repo.create(_build_pick(event.id, "h2h", "home", None), generated_at=at)
    assert a.id == b.id


def test_pick_repo_distinct_lines_are_separate_picks(session: Session) -> None:
    event = _add_event(session)
    repo = PickRepo(session)
    at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    over, _ = repo.create(_build_pick(event.id, "totals", "over", 2.5), generated_at=at)
    under, _ = repo.create(_build_pick(event.id, "totals", "over", 3.5), generated_at=at)
    assert over.id != under.id


def test_pick_repo_list_by_status(session: Session) -> None:
    event = _add_event(session)
    repo = PickRepo(session)
    repo.create(_build_pick(event.id, "h2h", "home"))
    pending = repo.list_by_status("pending")
    assert len(pending) == 1
    assert repo.list_by_status("won") == []


# --- SystemStateRepo ----------------------------------------------------------


def test_system_state_repo_creates_singleton(session: Session) -> None:
    state = SystemStateRepo(session).get()
    assert state.id == 1
    assert state.is_paused is False


def test_system_state_repo_is_idempotent(session: Session) -> None:
    repo = SystemStateRepo(session)
    first = repo.get()
    second = repo.get()
    assert first is second
    count = session.execute(select(func.count()).select_from(SystemState)).scalar()
    assert count == 1


def test_system_state_repo_pause_sets_flag_reason_and_timestamp(session: Session) -> None:
    repo = SystemStateRepo(session)
    state = repo.pause(reason="manual pause via Telegram")
    assert state.is_paused is True
    assert state.paused_reason == "manual pause via Telegram"
    assert state.paused_at is not None
    # Verifica persistencia: una nueva session ve el estado pausado.
    fresh = SystemStateRepo(session).get()
    assert fresh.is_paused is True
    assert fresh.paused_reason == "manual pause via Telegram"


def test_system_state_repo_resume_clears_pause_fields(session: Session) -> None:
    repo = SystemStateRepo(session)
    repo.pause(reason="test")
    state = repo.resume()
    assert state.is_paused is False
    assert state.paused_reason is None
    assert state.paused_at is None


def test_system_state_repo_pause_twice_overwrites_reason(session: Session) -> None:
    # Pausar dos veces no apila — la segunda razón gana.
    repo = SystemStateRepo(session)
    repo.pause(reason="first reason")
    state = repo.pause(reason="second reason")
    assert state.paused_reason == "second reason"


def test_system_state_repo_resume_when_not_paused_is_noop(session: Session) -> None:
    # Resume sobre estado limpio no falla.
    repo = SystemStateRepo(session)
    state = repo.resume()
    assert state.is_paused is False


# --- EventRepo (Etapa 3) / QuotaRepo ------------------------------------------


def test_event_repo_get_by_api_football_id(session: Session) -> None:
    event = _add_event(session)
    event.api_football_id = 1208021
    EventRepo(session).update(event)
    assert EventRepo(session).get_by_api_football_id(1208021) is event
    assert EventRepo(session).get_by_api_football_id(999999) is None


def test_quota_repo_add(session: Session) -> None:
    log = QuotaRepo(session).add(
        ApiQuotaLog(
            provider="odds_api",
            requests_remaining=499,
            requests_used=1,
            endpoint="/v4/sports/soccer_epl/odds/",
        )
    )
    assert log.id is not None
    assert log.provider == "odds_api"
