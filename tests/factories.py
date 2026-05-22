"""Constructores de entidades y carga de fixtures para tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from betting_bot.ingestion.schemas import (
    ApiFootballFixture,
    FixtureGoals,
    FixtureInfo,
    FixtureLeague,
    FixtureStatus,
    FixtureTeam,
    FixtureTeams,
    OddsApiEvent,
    OddsBookmaker,
    OddsMarket,
    OddsOutcome,
)
from betting_bot.persistence.models import Event, Pick

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture_json(name: str) -> Any:
    """Carga y parsea un archivo JSON de `tests/fixtures/`."""
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def build_event(**overrides: Any) -> Event:
    """Event con campos requeridos en valores dummy."""
    defaults: dict[str, Any] = {
        "league_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": datetime(2026, 5, 25, 19, 0, tzinfo=UTC),
        "status": "scheduled",
    }
    return Event(**{**defaults, **overrides})


def build_pick(event_id: str, **overrides: Any) -> Pick:
    """Pick con campos requeridos en valores dummy.

    `generated_at`/`generated_date` no se setean — los pone `PickRepo.create`.
    Pasalos como override si insertás el Pick directo por la sesión.
    """
    defaults: dict[str, Any] = {
        "event_id": event_id,
        "market_key": "h2h",
        "outcome": "home",
        "line": None,
        "reference_book": "pinnacle",
        "reference_price": 2.0,
        "reference_prob": 0.52,
        "devigging_method": "shin",
        "comparison_book": "bet365",
        "comparison_price": 2.15,
        "min_odds_for_value": 2.05,
        "ev_at_comparison": 0.05,
        "kelly_fraction": 0.012,
        "stake_recommended": 30000,
        "stake_pct_of_bankroll": 0.012,
        "bankroll_at_generation": 2500000,
    }
    return Pick(**{**defaults, **overrides})


def build_odds_event(
    *,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    event_id: str = "odds-evt-1",
    bookmakers: list[OddsBookmaker] | None = None,
) -> OddsApiEvent:
    """Evento de the-odds-api (modelo Pydantic) para tests."""
    return OddsApiEvent(
        id=event_id,
        sport_key="soccer_epl",
        commence_time=commence_time,
        home_team=home_team,
        away_team=away_team,
        bookmakers=bookmakers or [],
    )


def build_fixture(
    *,
    home_team: str,
    away_team: str,
    date: datetime,
    fixture_id: int = 1,
    home_team_id: int = 1,
    away_team_id: int = 2,
    league_id: int = 39,
    season: int = 2026,
    status_short: str = "NS",
) -> ApiFootballFixture:
    """Fixture de api-football (modelo Pydantic) para tests."""
    return ApiFootballFixture(
        fixture=FixtureInfo(
            id=fixture_id,
            date=date,
            status=FixtureStatus(long="Not Started", short=status_short),
        ),
        league=FixtureLeague(id=league_id, name="Premier League", season=season),
        teams=FixtureTeams(
            home=FixtureTeam(id=home_team_id, name=home_team),
            away=FixtureTeam(id=away_team_id, name=away_team),
        ),
        goals=FixtureGoals(),
    )


def build_h2h_bookmaker(
    *,
    key: str,
    home_team: str,
    away_team: str,
    home_price: float = 2.0,
    draw_price: float = 3.4,
    away_price: float = 3.8,
) -> OddsBookmaker:
    """Bookmaker con un mercado h2h (modelo Pydantic) para tests."""
    ts = datetime(2026, 5, 20, 22, 0, tzinfo=UTC)
    return OddsBookmaker(
        key=key,
        title=key.capitalize(),
        last_update=ts,
        markets=[
            OddsMarket(
                key="h2h",
                last_update=ts,
                outcomes=[
                    OddsOutcome(name=home_team, price=home_price),
                    OddsOutcome(name=away_team, price=away_price),
                    OddsOutcome(name="Draw", price=draw_price),
                ],
            )
        ],
    )
