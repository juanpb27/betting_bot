"""Tests de los clientes HTTP de ingesta, con `httpx.MockTransport` (sin red)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from betting_bot.ingestion._http import request_with_retries
from betting_bot.ingestion.fixtures import (
    ApiFootballClient,
    ApiFootballError,
    current_season,
)
from betting_bot.ingestion.odds import OddsApiClient, OddsApiError
from tests.factories import load_fixture_json

_Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: _Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- OddsApiClient ------------------------------------------------------------


async def test_odds_client_parses_events_and_quota() -> None:
    payload = load_fixture_json("odds_api_epl_h2h_sample.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=payload,
            headers={"x-requests-remaining": "499", "x-requests-used": "1"},
        )

    async with _client(handler) as http:
        events, quota = await OddsApiClient(http, "KEY").fetch_odds(
            "soccer_epl", ["h2h"], ["pinnacle", "betsson"]
        )

    assert len(events) == 3
    assert quota.provider == "odds_api"
    assert quota.requests_remaining == 499
    assert quota.requests_used == 1


async def test_odds_client_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid key"})

    async with _client(handler) as http:
        with pytest.raises(OddsApiError):
            await OddsApiClient(http, "BAD").fetch_odds("soccer_epl", ["h2h"], ["pinnacle"])


# --- ApiFootballClient --------------------------------------------------------


async def test_fixtures_client_parses_fixtures() -> None:
    payload = load_fixture_json("api_football_epl_fixtures_sample.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as http:
        fixtures, quota = await ApiFootballClient(http, "KEY").fetch_fixtures(39, 2024)

    assert len(fixtures) == 5
    assert quota.provider == "api_football"
    # /fixtures no reporta cuota usable en headers.
    assert quota.requests_remaining is None


async def test_fixtures_client_raises_on_api_errors_in_body() -> None:
    # api-football devuelve HTTP 200 con `errors` poblado ante plan/key inválido.
    def handler(request: httpx.Request) -> httpx.Response:
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

    async with _client(handler) as http:
        with pytest.raises(ApiFootballError, match="plan"):
            await ApiFootballClient(http, "KEY").fetch_fixtures(39, 2024)


# --- Reintentos y season ------------------------------------------------------


async def test_request_with_retries_recovers_from_transient_5xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as http:
        response = await request_with_retries(
            http, "GET", "https://example.test/x", backoff_seconds=0.0
        )

    assert response.status_code == 200
    assert calls["n"] == 2


def test_current_season() -> None:
    assert current_season(date(2026, 8, 1)) == 2026
    assert current_season(date(2026, 3, 1)) == 2025
