"""Cliente de the-odds-api: trae las cuotas de los mercados de una liga."""
from __future__ import annotations

import httpx

from betting_bot.ingestion._http import QuotaInfo, request_with_retries
from betting_bot.ingestion.schemas import OddsApiEvent, parse_odds_events

_BASE_URL = "https://api.the-odds-api.com/v4"


class OddsApiError(RuntimeError):
    """Falla al consultar the-odds-api."""


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class OddsApiClient:
    """Wrapper async sobre the-odds-api. Recibe el `httpx.AsyncClient` inyectado."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    async def fetch_odds(
        self, league_key: str, markets: list[str], bookmakers: list[str]
    ) -> tuple[list[OddsApiEvent], QuotaInfo]:
        """Trae las cuotas de una liga. Devuelve los eventos parseados y la cuota.

        No persiste nada — separa el I/O de red del I/O de DB. El consumo de cuota
        sale de los headers `x-requests-remaining`/`x-requests-used`.
        """
        endpoint = f"/sports/{league_key}/odds/"
        response = await request_with_retries(
            self._client,
            "GET",
            f"{_BASE_URL}{endpoint}",
            params={
                "apiKey": self._api_key,
                "regions": "eu",
                "markets": ",".join(markets),
                "bookmakers": ",".join(bookmakers),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=20,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OddsApiError(
                f"the-odds-api {endpoint}: HTTP {exc.response.status_code} "
                f"{exc.response.text[:200]}"
            ) from exc

        events = parse_odds_events(response.json())
        quota = QuotaInfo(
            provider="odds_api",
            endpoint=endpoint,
            requests_remaining=_to_int(response.headers.get("x-requests-remaining")),
            requests_used=_to_int(response.headers.get("x-requests-used")),
        )
        return events, quota
