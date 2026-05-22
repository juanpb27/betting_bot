"""Tests del pipeline de ingesta (`run_ingestion`) con clientes mockeados."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from betting_bot.cli.run_pipeline import run_ingestion
from betting_bot.ingestion.fixtures import ApiFootballClient
from betting_bot.ingestion.odds import OddsApiClient
from betting_bot.ingestion.schemas import (
    ApiFootballFixture,
    ApiFootballResponse,
    OddsApiEvent,
    OddsBookmaker,
    OddsMarket,
    OddsOutcome,
)
from betting_bot.persistence.models import ApiQuotaLog, Event, OddsSnapshot
from betting_bot.yaml_config import LeagueConfig
from tests.factories import build_fixture, build_h2h_bookmaker, build_odds_event

_WHEN = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
_LEAGUE = LeagueConfig(key="soccer_epl", api_football_id=39)


def _odds_payload(events: list[OddsApiEvent]) -> list[Any]:
    return [event.model_dump(mode="json") for event in events]


def _fixtures_payload(fixtures: list[ApiFootballFixture]) -> Any:
    resp = ApiFootballResponse(errors=[], results=len(fixtures), response=fixtures)
    return resp.model_dump(mode="json")


def _mock_http(
    odds_events: list[OddsApiEvent], fixtures: list[ApiFootballFixture]
) -> httpx.AsyncClient:
    """Cliente httpx que devuelve datos crafteados según el host del request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "the-odds-api" in str(request.url):
            return httpx.Response(
                200,
                json=_odds_payload(odds_events),
                headers={"x-requests-remaining": "490", "x-requests-used": "10"},
            )
        return httpx.Response(200, json=_fixtures_payload(fixtures))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _ingest(
    session: Session,
    odds_events: list[OddsApiEvent],
    fixtures: list[ApiFootballFixture],
) -> Any:
    async with _mock_http(odds_events, fixtures) as http:
        return await run_ingestion(
            session=session,
            odds_client=OddsApiClient(http, "K"),
            fixtures_client=ApiFootballClient(http, "K"),
            leagues=[_LEAGUE],
            bookmakers=["pinnacle"],
            season=2026,
        )


def _matching_pair() -> tuple[list[OddsApiEvent], list[ApiFootballFixture]]:
    """Un evento de odds y un fixture de api-football que sí matchean."""
    event = build_odds_event(
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=_WHEN,
        event_id="odds-1",
        bookmakers=[
            build_h2h_bookmaker(key="pinnacle", home_team="Arsenal", away_team="Chelsea")
        ],
    )
    fixture = build_fixture(
        home_team="Arsenal", away_team="Chelsea", date=_WHEN, fixture_id=555
    )
    return [event], [fixture]


async def test_ingestion_persists_event_odds_and_quota(session: Session) -> None:
    odds_events, fixtures = _matching_pair()
    result = await _ingest(session, odds_events, fixtures)

    assert result.leagues_processed == 1
    assert result.events_ingested == 1
    assert result.odds_snapshots == 3  # 1 bookmaker × 3 outcomes h2h

    assert session.execute(select(func.count()).select_from(Event)).scalar() == 1
    assert session.execute(select(func.count()).select_from(OddsSnapshot)).scalar() == 3
    # 2 logs de cuota por liga: uno de fixtures, uno de odds.
    assert session.execute(select(func.count()).select_from(ApiQuotaLog)).scalar() == 2

    event = session.execute(select(Event)).scalar_one()
    assert event.odds_api_id == "odds-1"
    assert event.api_football_id == 555
    assert event.status == "scheduled"


async def test_ingestion_maps_h2h_outcomes(session: Session) -> None:
    odds_events, fixtures = _matching_pair()
    await _ingest(session, odds_events, fixtures)
    outcomes = set(
        session.execute(select(OddsSnapshot.outcome)).scalars().all()
    )
    assert outcomes == {"home", "draw", "away"}


async def test_ingestion_skips_unmatched_event(session: Session) -> None:
    odds_events, _ = _matching_pair()
    # Fixture de equipos distintos → no matchea → se saltea el evento.
    other = [build_fixture(home_team="Liverpool", away_team="Everton", date=_WHEN)]
    result = await _ingest(session, odds_events, other)

    assert result.events_ingested == 0
    assert result.events_skipped == 1
    assert session.execute(select(func.count()).select_from(Event)).scalar() == 0


async def test_ingestion_is_idempotent_for_events(session: Session) -> None:
    odds_events, fixtures = _matching_pair()
    await _ingest(session, odds_events, fixtures)
    await _ingest(session, odds_events, fixtures)

    # El evento no se duplica (upsert por odds_api_id)...
    assert session.execute(select(func.count()).select_from(Event)).scalar() == 1
    # ...pero los snapshots de odds sí se acumulan (son una serie temporal).
    assert session.execute(select(func.count()).select_from(OddsSnapshot)).scalar() == 6


async def test_ingestion_isolates_a_league_failure(session: Session) -> None:
    # El fallo de una liga no debe tumbar la corrida de las demás.
    odds_events, fixtures = _matching_pair()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "the-odds-api" in url:
            return httpx.Response(
                200,
                json=_odds_payload(odds_events),
                headers={"x-requests-remaining": "490", "x-requests-used": "10"},
            )
        # api-football: la liga 39 falla (errors en el body), la 140 responde bien.
        if "league=39" in url:
            return httpx.Response(
                200,
                json={
                    "get": "fixtures",
                    "errors": {"plan": "expired"},
                    "results": 0,
                    "paging": {"current": 1, "total": 1},
                    "response": [],
                },
            )
        return httpx.Response(200, json=_fixtures_payload(fixtures))

    leagues = [
        LeagueConfig(key="soccer_epl", api_football_id=39),
        LeagueConfig(key="soccer_spain_la_liga", api_football_id=140),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run_ingestion(
            session=session,
            odds_client=OddsApiClient(http, "K"),
            fixtures_client=ApiFootballClient(http, "K"),
            leagues=leagues,
            bookmakers=["pinnacle"],
            season=2026,
        )

    assert len(result.leagues_failed) == 1
    assert "soccer_epl" in result.leagues_failed[0]
    # La otra liga se procesó igual.
    assert result.leagues_processed == 1
    assert result.events_ingested == 1
    assert session.execute(select(func.count()).select_from(Event)).scalar() == 1


async def test_ingestion_counts_unmapped_outcomes(session: Session) -> None:
    _, fixtures = _matching_pair()
    bookmaker = OddsBookmaker(
        key="pinnacle",
        title="Pinnacle",
        last_update=_WHEN,
        markets=[
            OddsMarket(
                key="h2h",
                last_update=_WHEN,
                outcomes=[
                    OddsOutcome(name="Arsenal", price=2.0),
                    OddsOutcome(name="Chelsea", price=3.8),
                    OddsOutcome(name="Draw", price=3.4),
                    OddsOutcome(name="Equipo Fantasma", price=99.0),  # no mapea
                ],
            )
        ],
    )
    event = build_odds_event(
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=_WHEN,
        event_id="odds-1",
        bookmakers=[bookmaker],
    )
    result = await _ingest(session, [event], fixtures)
    assert result.unmapped_outcomes == 1
    assert result.odds_snapshots == 3
