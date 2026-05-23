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
from telegram import Bot
from telegram.constants import ParseMode

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.config import get_settings
from betting_bot.delivery.pick_notifier import (
    build_pick_keyboard,
    build_pick_message,
)
from betting_bot.ingestion._http import QuotaInfo
from betting_bot.ingestion.fixtures import (
    ApiFootballClient,
    ApiFootballError,
    current_season,
)
from betting_bot.ingestion.normalizer import match_events
from betting_bot.ingestion.odds import OddsApiClient, OddsApiError
from betting_bot.ingestion.schemas import OddsApiEvent
from betting_bot.logging_setup import bind_request_id, configure_logging, get_logger
from betting_bot.persistence.db import session_scope
from betting_bot.persistence.models import ApiQuotaLog, Event, OddsSnapshot
from betting_bot.persistence.repo import (
    EventRepo,
    OddsRepo,
    PendingSheetsSyncRepo,
    PickRepo,
    QuotaRepo,
    SystemStateRepo,
)
from betting_bot.pricing.picks import SUPPORTED_MARKET_KEYS, generate_picks_for_event
from betting_bot.yaml_config import (
    LeagueConfig,
    load_active_leagues,
    load_active_markets,
    load_comparison_book_keys,
    load_notification_config,
    load_odds_bookmakers,
    load_quality_gates,
    load_sharp_reference_key,
    load_staking_config,
    load_yaml,
)

_H2H = "h2h"
_log = get_logger(__name__)


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
    # IDs de los eventos que ESTA corrida tocó (nuevos o re-snapshoteados).
    # Se usa para filtrar el pricing solo a estos; sin esto, pricing re-evalúa
    # eventos viejos cuyos snapshots ya no son de esta corrida.
    event_ids_touched: set[str] = field(default_factory=set)


@dataclass
class PricingResult:
    """Resumen de la fase de pricing + delivery (solo en modo --full)."""

    events_evaluated: int = 0
    picks_generated: int = 0  # incluye picks ya existentes (idempotentes)
    picks_new: int = 0  # los que efectivamente se persistieron como nuevos
    picks_notified: int = 0
    notify_errors: list[str] = field(default_factory=list)


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
            result.event_ids_touched.add(event.id)

    return result


async def run_pricing_and_notify(
    *,
    session: Session,
    events: list[Event],
    bot: Bot | None,
    chat_id: int | None,
) -> PricingResult:
    """Para cada evento ingerido en esta corrida: lee sus odds_snapshots,
    corre `generate_picks_for_event`, persiste los picks nuevos vía
    `PickRepo.create` (idempotente), notifica al chat autorizado, encola
    sync a Sheets. `bot` y `chat_id` son `None` solo en tests (skip notify).
    """
    result = PricingResult()
    all_active = load_active_markets()
    # Filtrá a los mercados que el orchestrator soporta hoy (h2h, btts). Los
    # demás (totals, spreads) viven en markets.yaml para Fase 2; el orchestrator
    # levantaría NotImplementedError si los recibe. Log warning una vez por
    # corrida para no perder visibilidad de la deuda.
    markets = [m for m in all_active if m.key in SUPPORTED_MARKET_KEYS]
    excluded = [m.key for m in all_active if m.key not in SUPPORTED_MARKET_KEYS]
    if excluded:
        _log.warning("pricing_markets_excluded_unsupported", markets=excluded)
    if not markets:
        _log.warning("pricing_skipped_no_supported_markets")
        return result
    sharp_ref = load_sharp_reference_key()
    comparison_books = load_comparison_book_keys()
    quality_gates = load_quality_gates()
    staking = load_staking_config()
    notif_cfg = load_notification_config()
    ledger = BankrollLedger(session)
    bankroll = ledger.get_total_balance()
    if bankroll <= 0:
        _log.warning("pricing_skipped_no_bankroll")
        return result

    pick_repo = PickRepo(session)
    odds_repo = OddsRepo(session)
    queue = PendingSheetsSyncRepo(session)

    for event in events:
        result.events_evaluated += 1
        snapshots = odds_repo.list_for_event(event.id)
        if not snapshots:
            continue
        try:
            picks = generate_picks_for_event(
                event=event,
                snapshots=snapshots,
                bankroll=bankroll,
                markets=markets,
                sharp_ref_key=sharp_ref,
                comparison_book_keys=comparison_books,
                quality_gates=quality_gates,
                staking=staking,
            )
        except (ValueError, NotImplementedError) as exc:
            _log.warning(
                "pricing_failed_for_event",
                event_id=event.id,
                error=repr(exc),
            )
            continue

        for pick in picks:
            result.picks_generated += 1
            created, is_new = pick_repo.create(pick)
            if not is_new:
                continue  # ya estaba persistido hoy → no re-notificar
            result.picks_new += 1
            queue.enqueue("pick", {"pick_id": created.id})
            # Notificación Telegram (si tenemos bot + chat).
            if bot is not None and chat_id is not None:
                try:
                    msg = build_pick_message(
                        pick=created,
                        event=event,
                        notification=notif_cfg,
                        staking=staking,
                        min_ev=next(
                            m.min_ev for m in markets if m.key == created.market_key
                        ),
                    )
                    await bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=build_pick_keyboard(created.id),
                    )
                    result.picks_notified += 1
                except Exception as exc:
                    # Falla de Telegram NO debe abortar la corrida; el pick
                    # ya está persistido y encolado para Sheets, podemos
                    # re-notificar manualmente más tarde.
                    msg = f"{event.id}: {type(exc).__name__}: {exc}"
                    result.notify_errors.append(msg)
                    _log.warning(
                        "pipeline_notify_failed",
                        event_id=event.id,
                        error=repr(exc),
                    )
    return result


async def _run(*, full: bool = False) -> None:
    with bind_request_id() as request_id:
        with session_scope() as session:
            state = SystemStateRepo(session).get()
            if state.is_paused:
                _log.warning(
                    "pipeline_aborted_paused",
                    paused_reason=state.paused_reason,
                    paused_at=state.paused_at.isoformat() if state.paused_at else None,
                )
                click.echo(
                    f"Sistema PAUSADO ({state.paused_reason or 'sin razón'}). "
                    f"Usa /resume para reanudar."
                )
                return

        settings = get_settings()
        leagues = load_active_leagues()
        bookmakers = load_odds_bookmakers()
        matching = load_yaml("thresholds.yaml")["matching"]

        _log.info("pipeline_start", leagues=len(leagues), bookmakers=len(bookmakers))

        # Bot standalone (no es la Application del listener — viven en
        # procesos distintos). Lo usamos solo en modo --full.
        bot: Bot | None = Bot(settings.telegram_bot_token) if full else None
        chat_id = settings.telegram_chat_id if full else None

        pricing_result: PricingResult | None = None
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
                if full:
                    # SOLO los eventos que esta corrida tocó (nuevos o
                    # re-snapshoteados); evita re-evaluar eventos viejos cuyos
                    # snapshots ya no son de esta corrida.
                    events = [
                        e
                        for e in (
                            session.get(Event, eid) for eid in result.event_ids_touched
                        )
                        if e is not None
                    ]
                    pricing_result = await run_pricing_and_notify(
                        session=session,
                        events=events,
                        bot=bot,
                        chat_id=chat_id,
                    )

        _log.info(
            "pipeline_done",
            leagues_processed=result.leagues_processed,
            leagues_failed=len(result.leagues_failed),
            events_ingested=result.events_ingested,
            events_skipped=result.events_skipped,
            odds_snapshots=result.odds_snapshots,
            picks_generated=pricing_result.picks_generated if pricing_result else 0,
            picks_new=pricing_result.picks_new if pricing_result else 0,
            picks_notified=pricing_result.picks_notified if pricing_result else 0,
        )
        _ = request_id  # silencia "unused" — el valor vive en contextvars
    _print_summary(result, pricing_result)


def _print_summary(
    result: IngestionResult, pricing: PricingResult | None = None
) -> None:
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
    if pricing is not None:
        click.echo(f"Eventos evaluados:  {pricing.events_evaluated}")
        click.echo(f"Picks generados:    {pricing.picks_generated}")
        click.echo(f"Picks nuevos:       {pricing.picks_new}")
        click.echo(f"Picks notificados:  {pricing.picks_notified}")
        for nerr in pricing.notify_errors:
            click.echo(f"  · notify error → {nerr}")


@click.command()
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Pipeline completo: ingestion + pricing + notificación Telegram + "
    "encolado a Sheets. Sin --full, corre solo la ingesta.",
)
def main(full: bool) -> None:
    """Corre el pipeline. `--full` activa pricing/notify/sheets sync."""
    configure_logging(level=get_settings().log_level)
    asyncio.run(_run(full=full))


if __name__ == "__main__":
    main()
