"""Tests de `assess_value` (TDD)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from betting_bot.pricing.value import ValueAssessment, assess_value


def test_assess_value_no_value_below_min_ev() -> None:
    # p=0.51 contra cuota 2.0 → EV=0.02, menor que min_ev=0.025.
    result = assess_value(0.51, 2.0, bankroll=1_000_000, min_ev=0.025)
    assert result.has_value is False
    assert result.stake_recommended == 0
    assert result.kelly_fraction == 0.0


def test_assess_value_populates_ev_and_min_odds() -> None:
    # EV = p*(odds-1) - (1-p) ; min_odds = (1 + min_ev) / p_real.
    result = assess_value(0.51, 2.0, bankroll=1_000_000, min_ev=0.025)
    assert abs(result.ev - 0.02) < 1e-10
    assert abs(result.min_odds_for_value - (1.025 / 0.51)) < 1e-10
    assert result.p_real == 0.51
    assert result.odds == 2.0


def test_assess_value_has_value_above_min_ev() -> None:
    # p=0.55, odds=2.0 → EV=0.10. Kelly/4 = 0.025 → stake = 25_000 sobre 1M.
    result = assess_value(0.55, 2.0, bankroll=1_000_000)
    assert result.has_value is True
    assert abs(result.ev - 0.10) < 1e-10
    assert abs(result.kelly_fraction - 0.025) < 1e-10
    assert result.stake_recommended == 25_000


def test_assess_value_min_odds_for_value_uses_min_ev() -> None:
    # min_odds = (1 + min_ev) / p_real ; con min_ev=0.025 y p=0.40 → 1.025/0.40.
    result = assess_value(0.40, 3.0, bankroll=1_000_000, min_ev=0.025)
    assert abs(result.min_odds_for_value - (1.025 / 0.40)) < 1e-10


def test_assess_value_min_odds_at_threshold_triggers_has_value() -> None:
    # Cuota == min_odds_for_value debe estar EXACTAMENTE al borde del threshold.
    p, min_ev = 0.50, 0.03
    threshold_odds = (1 + min_ev) / p
    result = assess_value(p, threshold_odds, bankroll=1_000_000, min_ev=min_ev)
    assert result.has_value is True
    assert abs(result.ev - min_ev) < 1e-10


def test_assess_value_rejects_non_positive_p_real() -> None:
    # Outcome imposible según el sharp → sin valor, sin levantar excepción.
    result = assess_value(0.0, 2.0, bankroll=1_000_000)
    assert result.has_value is False
    assert result.stake_recommended == 0
    assert result.min_odds_for_value == float("inf")


def test_assess_value_caps_stake_at_cap_pct() -> None:
    # Edge enorme: cap=0.03 sobre 1M = 30_000.
    result = assess_value(0.95, 2.0, bankroll=1_000_000, cap_pct=0.03)
    assert result.stake_recommended == 30_000
    assert result.has_value is True


def test_assess_value_returns_zero_stake_below_floor_even_with_value() -> None:
    # min_ev bajo (0.001) deja pasar has_value=True, pero kelly_frac < floor → stake 0.
    result = assess_value(
        0.5025, 2.0, bankroll=1_000_000, min_ev=0.001, floor_pct=0.003
    )
    assert result.has_value is True  # ev = 0.005 ≥ 0.001
    assert result.stake_recommended == 0  # kelly = 0.00125 < floor


def test_assess_value_returns_frozen_dataclass() -> None:
    result = assess_value(0.55, 2.0, bankroll=1_000_000)
    assert isinstance(result, ValueAssessment)
    with pytest.raises(FrozenInstanceError):
        result.p_real = 0.99  # type: ignore[misc]


def test_assess_value_respects_kelly_divisor() -> None:
    # Kelly/2 debe dar el doble de stake que Kelly/4 (mismo p, odds, bankroll).
    quarter = assess_value(0.55, 2.0, bankroll=1_000_000, kelly_divisor=4.0)
    half = assess_value(0.55, 2.0, bankroll=1_000_000, kelly_divisor=2.0, cap_pct=1.0)
    assert half.stake_recommended == 2 * quarter.stake_recommended
