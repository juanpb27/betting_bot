"""Pipeline principal: Trae fixtures (api-football) + odds h2h (the-odds-api) de cada liga
activa, cruza los eventos de ambas fuentes y los persiste en `events`, `odds_snapshots` y `api_quota_log`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import click
import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from betting_bot.config import get_settings
from betting_bot.ingestion._http import QuotaInfo
from betting_bot.ingestion.fixtures import (
    ApiFootballClient,
    ApiFootballError,
    current_season,
)
from betting_bot.ingestion.normalizer import match_events
from betting_bot.ingestion.odds import OddsApiClient, OddsApiError
from betting_bot.ingestion.schemas import OddsApiEvent
from betting_bot.persistence.db import session_scope
from betting_bot.persistence.models import ApiQuotaLog, Event, OddsSnapshot
from betting_bot.persistence.repo import EventRepo, OddsRepo, QuotaRepo
from betting_bot.yaml_config import (
    LeagueConfig,
    load_active_leagues,
    load_odds_bookmakers,
    load_yaml,
)

_H2H = "h2h"


@dataclass
class IngestionResult:
    """Resumen de una corrida de ingesta."""

    leagues_processed: int = 0
    leagues_failed: list[str] = field(default_factory=list)  # liga: error
    events_ingested: int = 0  # nuevos + actualizados
    events_skipped: int = 0  # sin match contra api-football
    odds_snapshots: int = 0
    unmapped_outcomes: int = 0  # outcomes h2h que no mapearon a home/draw/away
    skipped: list[str] = field(default_factory=list)


def _map_h2h_outcome(name: str, event: OddsApiEvent) -> str | None:
    """Traduce el `name` de un outcome de the-odds-api a home/draw/away."""
    if name == event.home_team:
        return "home"
    if name == event.away_team:
        return "away"
    if name.lower() == "draw":
        return "draw"
    return None


def _build_snapshots(
    event_id: str, odds_event: OddsApiEvent, captured_at: datetime
) -> tuple[list[OddsSnapshot], int]:
    """Construye los OddsSnapshot de un evento (un snapshot por bookmaker × outcome h2h).

    Devuelve `(snapshots, no_mapeados)`: `no_mapeados` cuenta los outcomes cuyo
    `name` no correspondió a home/draw/away — no debería pasar, pero si pasa hay
    que verlo, no descartarlo en silencio.
    """
    snapshots: list[OddsSnapshot] = []
    unmapped = 0
    for bookmaker in odds_event.bookmakers:
        for market in bookmaker.markets:
            if market.key != _H2H:
                continue
            for outcome in market.outcomes:
                mapped = _map_h2h_outcome(outcome.name, odds_event)
                if mapped is None:
                    unmapped += 1
                    continue
                snapshots.append(
                    OddsSnapshot(
                        event_id=event_id,
                        bookmaker_key=bookmaker.key,
                        market_key=_H2H,
                        outcome=mapped,
                        line=outcome.point,
                        price=outcome.price,
                        captured_at=captured_at,
                    )
                )
    return snapshots, unmapped


def _quota_log(quota: QuotaInfo) -> ApiQuotaLog:
    return ApiQuotaLog(
        provider=quota.provider,
        requests_remaining=quota.requests_remaining,
        requests_used=quota.requests_used,
        requests_limit=quota.requests_limit,
        endpoint=quota.endpoint,
    )


async def run_ingestion(
    *,
    session: Session,
    odds_client: OddsApiClient,
    fixtures_client: ApiFootballClient,
    leagues: list[LeagueConfig],
    bookmakers: list[str],
    season: int,
    min_confidence: float = 90.0,
    time_window_hours: float = 6.0,
) -> IngestionResult:
    """Ingiere fixtures + odds h2h de cada liga y los persiste.

    Por liga: trae fixtures y odds en paralelo, registra la cuota, cruza cada
    evento de the-odds-api con su fixture de api-football (skip si no hay match),
    hace upsert del evento y guarda un snapshot de odds por bookmaker/outcome.
    El llamador controla la transacción (no se commitea acá).
    """
    event_repo = EventRepo(session)
    odds_repo = OddsRepo(session)
    quota_repo = QuotaRepo(session)
    result = IngestionResult()

    for league in leagues:
        try:
            (fixtures, fx_quota), (events, odds_quota) = await asyncio.gather(
                fixtures_client.fetch_fixtures(league.api_football_id, season),
                odds_client.fetch_odds(league.key, [_H2H], bookmakers),
            )
        except (OddsApiError, ApiFootballError, httpx.HTTPError, ValidationError) as exc:
            # El fallo de una liga (API caída, key inválida, respuesta inesperada)
            # NO debe tumbar la corrida de las demás ligas: se registra y se sigue.
            result.leagues_failed.append(f"{league.key}: {type(exc).__name__}: {exc}")
            continue

        quota_repo.add(_quota_log(fx_quota))
        quota_repo.add(_quota_log(odds_quota))
        result.leagues_processed += 1
        captured_at = datetime.now(UTC)

        for odds_event in events:
            match = match_events(
                odds_event,
                fixtures,
                min_confidence=min_confidence,
                time_window_hours=time_window_hours,
            )
            if match is None:
                result.events_skipped += 1
                result.skipped.append(
                    f"{league.key}: {odds_event.home_team} vs {odds_event.away_team}"
                )
                continue

            event = event_repo.get_by_odds_api_id(odds_event.id)
            if event is None:
                event = Event(
                    odds_api_id=odds_event.id,
                    api_football_id=match.fixture.fixture.id,
                    league_key=league.key,
                    home_team=odds_event.home_team,
                    away_team=odds_event.away_team,
                    commence_time=odds_event.commence_time,
                    status="scheduled",
                )
                event_repo.add(event)
            else:
                event.api_football_id = match.fixture.fixture.id
                event_repo.update(event)
            result.events_ingested += 1

            snapshots, unmapped = _build_snapshots(event.id, odds_event, captured_at)
            odds_repo.add_many(snapshots)
            result.odds_snapshots += len(snapshots)
            result.unmapped_outcomes += unmapped

    return result


async def _run() -> None:
    settings = get_settings()
    leagues = load_active_leagues()
    bookmakers = load_odds_bookmakers()
    matching = load_yaml("thresholds.yaml")["matching"]

    async with httpx.AsyncClient() as http:
        odds_client = OddsApiClient(http, settings.odds_api_key)
        fixtures_client = ApiFootballClient(http, settings.api_football_key)
        with session_scope() as session:
            result = await run_ingestion(
                session=session,
                odds_client=odds_client,
                fixtures_client=fixtures_client,
                leagues=leagues,
                bookmakers=bookmakers,
                season=current_season(),
                min_confidence=float(matching["min_team_match_confidence"]),
                time_window_hours=float(matching["time_window_hours"]),
            )
    _print_summary(result)


def _print_summary(result: IngestionResult) -> None:
    click.echo(f"Ligas procesadas:   {result.leagues_processed}")
    click.echo(f"Ligas con fallo:    {len(result.leagues_failed)}")
    click.echo(f"Eventos ingeridos:  {result.events_ingested}")
    click.echo(f"Eventos sin match:  {result.events_skipped}")
    click.echo(f"Snapshots de odds:  {result.odds_snapshots}")
    if result.unmapped_outcomes:
        click.echo(f"Outcomes no mapeados: {result.unmapped_outcomes}")
    for failure in result.leagues_failed:
        click.echo(f"  · liga con fallo → {failure}")
    for skip in result.skipped:
        click.echo(f"  · sin match → {skip}")


@click.command()
@click.option("--ingest-only", is_flag=True, help="Solo ingesta, sin pricing.")
def main(ingest_only: bool) -> None:
    """Corre el pipeline. Etapa 3: solo `--ingest-only` está implementado."""
    if not ingest_only:
        raise click.ClickException("Solo --ingest-only está implementado (Etapa 3).")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
