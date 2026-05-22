"""Cliente de api-football: trae el calendario de partidos de una liga."""
from __future__ import annotations

from datetime import date

import httpx

from betting_bot.ingestion._http import QuotaInfo, request_with_retries
from betting_bot.ingestion.schemas import ApiFootballFixture, parse_fixtures_response

_BASE_URL = "https://v3.football.api-sports.io"


class ApiFootballError(RuntimeError):
    """Falla al consultar api-football."""


def current_season(today: date | None = None) -> int:
    """Temporada vigente de api-football.

    La temporada se identifica por el año en que arranca: si estamos en julio o
    después, es el año actual; si no, el anterior.
    """
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


class ApiFootballClient:
    """Wrapper async sobre api-football. Recibe el `httpx.AsyncClient` inyectado."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    async def fetch_fixtures(
        self, league_id: int, season: int
    ) -> tuple[list[ApiFootballFixture], QuotaInfo]:
        """Trae los fixtures de una liga/temporada. No persiste."""
        endpoint = "/fixtures"
        response = await request_with_retries(
            self._client,
            "GET",
            f"{_BASE_URL}{endpoint}",
            headers={"x-apisports-key": self._api_key},
            params={"league": league_id, "season": season},
            timeout=20,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiFootballError(
                f"api-football {endpoint}: HTTP {exc.response.status_code}"
            ) from exc

        parsed = parse_fixtures_response(response.json())
        # api-football devuelve HTTP 200 con `errors` poblado ante key/plan inválido;
        # raise_for_status() no lo detecta.
        if parsed.errors:
            raise ApiFootballError(f"api-football {endpoint}: {parsed.errors}")

        # `/fixtures` no expone la cuota diaria en headers usables → requests_* None.
        quota = QuotaInfo(provider="api_football", endpoint=endpoint)
        return parsed.response, quota
