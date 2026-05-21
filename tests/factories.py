"""Constructores de entidades para tests. Centraliza los valores dummy."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from betting_bot.persistence.models import Event, Pick


def build_event(**overrides: Any) -> Event:
    """Event con campos requeridos en valores dummy."""
    defaults: dict[str, Any] = {
        "league_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": datetime(2026, 5, 25, 19, 0, tzinfo=UTC),
        "status": "scheduled",
    }
    return Event(**{**defaults, **overrides})


def build_pick(event_id: str, **overrides: Any) -> Pick:
    """Pick con campos requeridos en valores dummy.

    `generated_at`/`generated_date` no se setean — los pone `PickRepo.create`.
    Pasalos como override si insertás el Pick directo por la sesión.
    """
    defaults: dict[str, Any] = {
        "event_id": event_id,
        "market_key": "h2h",
        "outcome": "home",
        "line": None,
        "reference_book": "pinnacle",
        "reference_price": 2.0,
        "reference_prob": 0.52,
        "devigging_method": "shin",
        "comparison_book": "bet365",
        "comparison_price": 2.15,
        "min_odds_for_value": 2.05,
        "ev_at_comparison": 0.05,
        "kelly_fraction": 0.012,
        "stake_recommended": 30000,
        "stake_pct_of_bankroll": 0.012,
        "bankroll_at_generation": 2500000,
    }
    return Pick(**{**defaults, **overrides})
