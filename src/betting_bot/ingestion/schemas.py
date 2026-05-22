"""Contratos Pydantic de las respuestas de las APIs externas.

Son la frontera tipada del sistema: las respuestas crudas de the-odds-api y
api-football se validan contra estos modelos antes de tocar cualquier otra capa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- the-odds-api: GET /v4/sports/{sport}/odds/ -------------------------------


class OddsOutcome(_ApiModel):
    name: str  # nombre del equipo, o "Draw" en h2h
    price: float  # cuota decimal
    point: float | None = None  # línea (totals/spreads); None en h2h


class OddsMarket(_ApiModel):
    key: str  # "h2h", "totals", "spreads", ...
    last_update: datetime
    outcomes: list[OddsOutcome]


class OddsBookmaker(_ApiModel):
    key: str
    title: str
    last_update: datetime
    markets: list[OddsMarket]


class OddsApiEvent(_ApiModel):
    id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[OddsBookmaker]


_ODDS_EVENT_LIST = TypeAdapter(list[OddsApiEvent])


def parse_odds_events(data: Any) -> list[OddsApiEvent]:
    """Valida la respuesta cruda de the-odds-api (un array de eventos)."""
    return _ODDS_EVENT_LIST.validate_python(data)


# --- api-football: GET /fixtures ----------------------------------------------


class FixtureStatus(_ApiModel):
    long: str  # "Match Finished", "Not Started", ...
    short: str  # "FT", "NS", ...
    elapsed: int | None = None


class FixtureInfo(_ApiModel):
    id: int
    date: datetime
    status: FixtureStatus


class FixtureLeague(_ApiModel):
    id: int
    name: str
    season: int


class FixtureTeam(_ApiModel):
    id: int
    name: str


class FixtureTeams(_ApiModel):
    home: FixtureTeam
    away: FixtureTeam


class FixtureGoals(_ApiModel):
    home: int | None = None
    away: int | None = None


class ApiFootballFixture(_ApiModel):
    fixture: FixtureInfo
    league: FixtureLeague
    teams: FixtureTeams
    goals: FixtureGoals


class ApiFootballResponse(_ApiModel):
    # api-football devuelve `errors` como [] cuando no hay y {...} cuando sí.
    errors: dict[str, str] | list[str]
    results: int
    response: list[ApiFootballFixture]


def parse_fixtures_response(data: Any) -> ApiFootballResponse:
    """Valida la respuesta cruda de api-football `/fixtures` (con envelope)."""
    return ApiFootballResponse.model_validate(data)
