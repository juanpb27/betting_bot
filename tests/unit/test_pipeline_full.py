"""Tests del wiring `--full` de `cli/run_pipeline.py`.

Validamos `run_pricing_and_notify` con DB real (in-memory) y Bot mockeado.
NO testeamos el `main` con click ni el `_run` async completo — eso requiere
mockear httpx y settings, y la lógica del wiring ya se cubre acá.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.orm import Session

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.cli.run_pipeline import run_pricing_and_notify
from betting_bot.persistence.models import Event, OddsSnapshot, PendingSheetsSync, Pick
from betting_bot.persistence.repo import EventRepo, OddsRepo


def _make_event_with_value_picks(session: Session) -> Event:
    """Setup: 1 evento con snapshots que GENERAN al menos 1 pick con EV+.
    Pinnacle ~ fair (p_home ≈ 0.50), bet365 paga 2.30 sobre home → EV 0.15."""
    event = Event(
        odds_api_id="evt-1",
        league_key="soccer_epl",
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=datetime.now(UTC) + timedelta(hours=24),
        status="scheduled",
    )
    EventRepo(session).add(event)
    odds_repo = OddsRepo(session)
    snaps = []
    captured = datetime.now(UTC)
    for outcome, sharp_price in [("home", 1.95), ("draw", 3.60), ("away", 4.10)]:
        snaps.append(OddsSnapshot(
            event_id=event.id, bookmaker_key="pinnacle",
            market_key="h2h", outcome=outcome, price=sharp_price,
            captured_at=captured,
        ))
    for outcome, p in [("home", 2.30), ("draw", 3.50), ("away", 4.00)]:
        snaps.append(OddsSnapshot(
            event_id=event.id, bookmaker_key="bet365",
            market_key="h2h", outcome=outcome, price=p, captured_at=captured,
        ))
    for outcome, p in [("home", 2.20), ("draw", 3.45), ("away", 3.90)]:
        snaps.append(OddsSnapshot(
            event_id=event.id, bookmaker_key="betsson",
            market_key="h2h", outcome=outcome, price=p, captured_at=captured,
        ))
    odds_repo.add_many(snaps)
    return event


async def test_run_pricing_persists_picks_and_enqueues_sheets(
    session: Session,
) -> None:
    BankrollLedger(session).record_deposit("betplay", 1_000_000)
    event = _make_event_with_value_picks(session)

    result = await run_pricing_and_notify(
        session=session, events=[event], bot=None, chat_id=None
    )

    assert result.events_evaluated == 1
    assert result.picks_new >= 1
    # Picks persistidos.
    picks = session.execute(select(Pick).where(Pick.event_id == event.id)).scalars().all()
    assert len(picks) >= 1
    # Encolado a sheets (1 enqueue por pick nuevo).
    queued = session.execute(
        select(PendingSheetsSync).where(PendingSheetsSync.payload_type == "pick")
    ).scalars().all()
    assert len(queued) == result.picks_new


async def test_run_pricing_is_idempotent_no_re_notify_on_second_run(
    session: Session,
) -> None:
    BankrollLedger(session).record_deposit("betplay", 1_000_000)
    event = _make_event_with_value_picks(session)

    first = await run_pricing_and_notify(
        session=session, events=[event], bot=None, chat_id=None
    )
    second = await run_pricing_and_notify(
        session=session, events=[event], bot=None, chat_id=None
    )
    # Segunda corrida no crea picks nuevos (idempotencia por
    # PickRepo.create).
    assert second.picks_new == 0
    assert second.picks_generated == first.picks_generated  # mismo total
    # Solo 1 enqueue total por pick (de la primera corrida).
    queued = session.execute(
        select(PendingSheetsSync).where(PendingSheetsSync.payload_type == "pick")
    ).scalars().all()
    assert len(queued) == first.picks_new


async def test_run_pricing_skips_when_bankroll_is_zero(session: Session) -> None:
    # Sin deposit → bankroll 0 → no se evalúan picks.
    event = _make_event_with_value_picks(session)
    result = await run_pricing_and_notify(
        session=session, events=[event], bot=None, chat_id=None
    )
    assert result.events_evaluated == 0
    assert result.picks_new == 0


async def test_run_pricing_sends_telegram_message_when_bot_provided(
    session: Session,
) -> None:
    BankrollLedger(session).record_deposit("betplay", 1_000_000)
    event = _make_event_with_value_picks(session)
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    result = await run_pricing_and_notify(
        session=session, events=[event], bot=fake_bot, chat_id=42
    )

    assert result.picks_notified == result.picks_new
    assert fake_bot.send_message.call_count == result.picks_new
    # El primer call llevó parse_mode + reply_markup.
    kwargs = fake_bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert "parse_mode" in kwargs
    assert "reply_markup" in kwargs


async def test_run_pricing_continues_if_telegram_fails(
    session: Session,
) -> None:
    BankrollLedger(session).record_deposit("betplay", 1_000_000)
    event = _make_event_with_value_picks(session)
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    result = await run_pricing_and_notify(
        session=session, events=[event], bot=fake_bot, chat_id=42
    )
    # Picks persistidos y encolados, pero notify_errors > 0 y picks_notified = 0.
    assert result.picks_new >= 1
    assert result.picks_notified == 0
    assert len(result.notify_errors) == result.picks_new
    # Pero los picks SÍ están en DB y SÍ están encolados.
    queued = session.execute(
        select(PendingSheetsSync).where(PendingSheetsSync.payload_type == "pick")
    ).scalars().all()
    assert len(queued) == result.picks_new
