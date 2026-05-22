"""Tests de integración de la ingesta: golpean las APIs reales.

Excluidos de la corrida normal (`addopts` filtra `not integration`). Para
correrlos: `uv run pytest -m integration`. Consumen cuota real de las APIs.
"""
from __future__ import annotations

import httpx
import pytest

from betting_bot.config import get_settings
from betting_bot.ingestion.fixtures import ApiFootballClient
from betting_bot.ingestion.odds import OddsApiClient
from betting_bot.yaml_config import load_odds_bookmakers

pytestmark = pytest.mark.integration


async def test_odds_client_live() -> None:
    """OddsApiClient contra the-odds-api real (el plan Free sirve para odds)."""
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        events, quota = await OddsApiClient(http, settings.odds_api_key).fetch_odds(
            "soccer_epl", ["h2h"], load_odds_bookmakers()
        )
    assert isinstance(events, list)
    assert quota.provider == "odds_api"
    assert quota.requests_remaining is not None


async def test_fixtures_client_live() -> None:
    """ApiFootballClient contra api-football real.

    Usa temporada 2024: accesible incluso en el plan Free. La temporada actual
    requiere plan Pro.
    """
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        fixtures, quota = await ApiFootballClient(
            http, settings.api_football_key
        ).fetch_fixtures(39, 2024)
    assert len(fixtures) > 0
    assert quota.provider == "api_football"
