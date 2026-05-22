"""Tests de los contratos Pydantic de las APIs externas (TDD).

Validan los modelos contra respuestas JSON reales capturadas en Etapa 1. Si una
API cambia la forma de un campo, estos tests fallan en vez de romper en prod.
"""
from __future__ import annotations

from datetime import datetime

from betting_bot.ingestion.schemas import (
    ApiFootballResponse,
    OddsApiEvent,
    parse_fixtures_response,
    parse_odds_events,
)
from tests.factories import load_fixture_json

# --- the-odds-api: /odds ------------------------------------------------------


def test_parse_odds_events_from_fixture() -> None:
    events = parse_odds_events(load_fixture_json("odds_api_epl_h2h_sample.json"))
    assert len(events) == 3
    assert all(isinstance(e, OddsApiEvent) for e in events)


def test_odds_event_top_level_fields() -> None:
    events = parse_odds_events(load_fixture_json("odds_api_epl_h2h_sample.json"))
    first = events[0]
    assert first.id == "aa1e33369fe2c3eb75c2414a4f3a46b3"
    assert first.sport_key == "soccer_epl"
    assert first.home_team == "Crystal Palace"
    assert first.away_team == "Arsenal"
    assert isinstance(first.commence_time, datetime)


def test_odds_event_bookmakers_markets_outcomes() -> None:
    events = parse_odds_events(load_fixture_json("odds_api_epl_h2h_sample.json"))
    pinnacle = next(b for b in events[0].bookmakers if b.key == "pinnacle")
    h2h = pinnacle.markets[0]
    assert h2h.key == "h2h"
    assert len(h2h.outcomes) == 3
    arsenal = next(o for o in h2h.outcomes if o.name == "Arsenal")
    assert arsenal.price == 1.82
    # h2h no tiene línea: `point` queda None.
    assert arsenal.point is None


# --- api-football: /fixtures --------------------------------------------------


def test_parse_fixtures_response_from_fixture() -> None:
    resp = parse_fixtures_response(
        load_fixture_json("api_football_epl_fixtures_sample.json")
    )
    assert isinstance(resp, ApiFootballResponse)
    assert resp.errors == []
    assert len(resp.response) == 5


def test_fixture_fields() -> None:
    resp = parse_fixtures_response(
        load_fixture_json("api_football_epl_fixtures_sample.json")
    )
    first = resp.response[0]
    assert first.fixture.id == 1208021
    assert first.league.id == 39
    assert first.league.season == 2024
    assert first.teams.home.name == "Manchester United"
    assert first.teams.away.name == "Fulham"
    assert first.fixture.status.short == "FT"
    assert isinstance(first.fixture.date, datetime)


def test_fixture_goals_parsed() -> None:
    resp = parse_fixtures_response(
        load_fixture_json("api_football_epl_fixtures_sample.json")
    )
    first = resp.response[0]
    assert first.goals.home == 1
    assert first.goals.away == 0
