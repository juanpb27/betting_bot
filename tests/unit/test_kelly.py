"""Tests de Kelly fraccional + cálculo de stake (TDD)."""
from __future__ import annotations

from betting_bot.pricing.kelly import calculate_stake, kelly_fraction

# --- kelly_fraction -----------------------------------------------------------


def test_kelly_fraction_zero_when_no_edge() -> None:
    # p=0.5 con cuota 1.8 → EV negativo.
    assert kelly_fraction(0.5, 1.8) == 0.0


def test_kelly_fraction_zero_at_exact_breakeven() -> None:
    # p == 1/odds → raw_kelly = 0.
    assert kelly_fraction(0.5, 2.0) == 0.0


def test_kelly_fraction_zero_for_degenerate_odds() -> None:
    # Cuota <= 1.0 es degenerada (no hay premio neto): nunca apostar.
    assert kelly_fraction(0.5, 1.0) == 0.0
    assert kelly_fraction(0.5, 0.99) == 0.0


def test_kelly_fraction_zero_for_non_positive_p() -> None:
    # p == 0 → no hay edge; guard explícito evita -inf / divisions raras.
    assert kelly_fraction(0.0, 2.0) == 0.0


def test_kelly_fraction_positive_when_edge_positive() -> None:
    # p=0.55, odds=2.0 → raw_kelly = 0.10, /4 = 0.025.
    f = kelly_fraction(0.55, 2.0, fraction=0.25)
    assert abs(f - 0.025) < 1e-10


def test_kelly_fraction_scales_with_fraction_param() -> None:
    # Kelly/2 debe ser el doble de Kelly/4.
    f_quarter = kelly_fraction(0.55, 2.0, fraction=0.25)
    f_half = kelly_fraction(0.55, 2.0, fraction=0.5)
    assert abs(f_half - 2 * f_quarter) < 1e-10


# --- calculate_stake ----------------------------------------------------------


def test_calculate_stake_zero_below_floor() -> None:
    # p apenas mayor que breakeven → kelly_frac = 0.0025 < floor=0.003 → stake 0.
    assert calculate_stake(1_000_000, 0.505, 2.0, floor=0.003) == 0


def test_calculate_stake_zero_when_no_edge() -> None:
    assert calculate_stake(1_000_000, 0.5, 2.0) == 0


def test_calculate_stake_caps_at_cap() -> None:
    # Edge enorme → kelly_frac sería > cap → se trunca a cap.
    stake = calculate_stake(1_000_000, 0.99, 2.0, cap=0.03, rounding_unit=1000)
    # cap=0.03 sobre 1M = 30_000 (ya múltiplo de 1000).
    assert stake == 30_000


def test_calculate_stake_rounds_half_up_not_bankers() -> None:
    # p=0.557, odds=2.0 → raw_kelly = 0.114, /4 = 0.0285, cap=0.03 no aplica.
    # Stake bruto = 1_000_000 * 0.0285 = 28_500 → 28.5 unidades de 1k.
    # ROUND_HALF_UP → 29_000. Banker's daría 28_000 (28 es par) — el test
    # falla si alguien revierte a `round()` builtin.
    stake = calculate_stake(1_000_000, 0.557, 2.0, rounding_unit=1000)
    assert stake == 29_000


def test_calculate_stake_rounds_half_up_below_half() -> None:
    # Stake bruto = 27_400 → 27.4 → ROUND_HALF_UP redondea hacia 27.
    stake = calculate_stake(1_000_000, 0.5548, 2.0, rounding_unit=1000)
    assert stake == 27_000


def test_calculate_stake_works_with_usd_rounding_unit_one() -> None:
    # Deployment USD: rounding_unit = 1 (dólares enteros).
    # bankroll=2500, p=0.555, odds=2.0 → kelly_frac=0.0275 → bruto=68.75 → 69.
    stake = calculate_stake(2500, 0.555, 2.0, rounding_unit=1)
    assert stake == 69


def test_calculate_stake_returns_int() -> None:
    # El campo Pick.stake_recommended es int — el cálculo devuelve int.
    stake = calculate_stake(1_000_000, 0.55, 2.0)
    assert isinstance(stake, int)
