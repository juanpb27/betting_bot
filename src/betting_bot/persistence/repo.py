"""Capa de acceso a datos sobre los modelos.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from betting_bot.config import get_settings
from betting_bot.persistence.models import Event, OddsSnapshot, Pick, SystemState


def _project_date(at: datetime) -> date:
    """Fecha calendario de `at` en la TZ del proyecto.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(ZoneInfo(get_settings().timezone)).date()


class EventRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: Event) -> Event:
        self._session.add(event)
        self._session.flush()
        return event

    def get(self, event_id: str) -> Event | None:
        return self._session.get(Event, event_id)

    def get_by_odds_api_id(self, odds_api_id: str) -> Event | None:
        return self._session.execute(
            select(Event).where(Event.odds_api_id == odds_api_id)
        ).scalar_one_or_none()


class OddsRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, snapshot: OddsSnapshot) -> OddsSnapshot:
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def add_many(self, snapshots: list[OddsSnapshot]) -> None:
        self._session.add_all(snapshots)
        self._session.flush()

    def list_for_event(self, event_id: str) -> list[OddsSnapshot]:
        return list(
            self._session.execute(
                select(OddsSnapshot)
                .where(OddsSnapshot.event_id == event_id)
                .order_by(OddsSnapshot.captured_at.desc())
            ).scalars()
        )


class PickRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, pick: Pick, *, generated_at: datetime | None = None) -> Pick:
        """Inserta un pick de forma idempotente.
        """
        at = generated_at or datetime.now(UTC)
        pick.generated_at = at
        pick.generated_date = _project_date(at)

        existing = self._find_duplicate(pick)
        if existing is not None:
            return existing

        self._session.add(pick)
        self._session.flush()
        return pick

    def _find_duplicate(self, pick: Pick) -> Pick | None:
        line_filter = Pick.line.is_(None) if pick.line is None else Pick.line == pick.line
        return self._session.execute(
            select(Pick).where(
                Pick.event_id == pick.event_id,
                Pick.market_key == pick.market_key,
                Pick.outcome == pick.outcome,
                line_filter,
                Pick.generated_date == pick.generated_date,
            )
        ).scalar_one_or_none()

    def get(self, pick_id: str) -> Pick | None:
        return self._session.get(Pick, pick_id)

    def list_by_status(self, status: str) -> list[Pick]:
        return list(
            self._session.execute(
                select(Pick).where(Pick.status == status).order_by(Pick.generated_at.desc())
            ).scalars()
        )


class SystemStateRepo:
    """Acceso al singleton `system_state` (id=1)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> SystemState:
        """Devuelve el estado del sistema, creándolo si aún no existe (id=1)."""
        state = self._session.get(SystemState, 1)
        if state is None:
            state = SystemState(id=1, is_paused=False)
            self._session.add(state)
            self._session.flush()
        return state
