"""Tests del de-vigging (TDD).
"""
from __future__ import annotations

import pytest

from betting_bot.pricing.devigging import devig_multiplicative, devig_shin

# --- Multiplicativo ---------------------------------------------------------


def test_devig_multiplicative_no_margin() -> None:
    result = devig_multiplicative([2.0, 2.0])
    assert abs(result[0] - 0.5) < 1e-10
    assert abs(result[1] - 0.5) < 1e-10


def test_devig_multiplicative_with_margin() -> None:
    result = devig_multiplicative([1.91, 1.91])
    assert abs(sum(result) - 1.0) < 1e-10
    assert abs(result[0] - result[1]) < 1e-10  # mercado simétrico


def test_devig_multiplicative_three_way_sums_to_one() -> None:
    result = devig_multiplicative([2.10, 3.40, 3.60])
    assert abs(sum(result) - 1.0) < 1e-10


def test_devig_multiplicative_preserves_order() -> None:
    # El favorito (cuota más baja) recibe la prob más alta.
    result = devig_multiplicative([1.40, 4.50, 8.00])
    assert result[0] > result[1] > result[2]


# --- Shin ------------------------------------------------------------------


def test_devig_shin_returns_zero_for_fair_market() -> None:
    fair, z = devig_shin([3.0, 3.0, 3.0])
    assert abs(z) < 1e-8
    for p in fair:
        assert abs(p - 1 / 3) < 1e-8


def test_devig_shin_corrects_favorite_longshot_bias() -> None:
    """Shin corrige el favorite-longshot bias del retail: asigna MÁS prob al
    favorito y MENOS al longshot que el multiplicativo. El multiplicativo
    reparte el overround uniformemente y deja el sesgo intacto; Shin asume
    insider trading y "desinfla" más a los longshots.

    Refs: Shin (1993, EJ 103, pp. 1141-1153); Štrumbelj (2014, IJF 30, eq. 5);
    Buchdahl — "How to remove the overround" (football-data.co.uk).
    """
    prices = [1.40, 4.50, 8.00]
    shin_probs, _ = devig_shin(prices)
    mult_probs = devig_multiplicative(prices)
    assert shin_probs[0] > mult_probs[0]   # favorito sube
    assert shin_probs[-1] < mult_probs[-1]  # longshot baja
    # Sanity: sigue cumpliendo el contrato global de sum==1.
    assert abs(sum(shin_probs) - 1.0) < 1e-8


def test_devig_shin_known_case() -> None:
    prices = [2.10, 3.40, 3.60]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
    assert 0 < z < 0.1


def test_devig_shin_extreme_favorite() -> None:
    """Caso extremo del RUNBOOK: Madrid 1.05 vs colero."""
    prices = [1.05, 12.0, 30.0]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
    assert fair[0] > 0.90
    assert all(p > 0 for p in fair)


def test_devig_shin_super_extreme() -> None:
    """Caso patológico del RUNBOOK para robustez del solver."""
    prices = [1.02, 25.0, 80.0]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
    assert all(p > 0 for p in fair)


def test_devig_shin_requires_at_least_two_outcomes() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        devig_shin([2.0])


@pytest.mark.parametrize(
    "prices",
    [
        [2.0, 2.0, 2.0],
        [1.50, 4.00, 6.00],
        [1.05, 12.0, 30.0],
        [1.02, 25.0, 80.0],
        [2.10, 3.40, 3.60],
    ],
)
def test_devig_shin_invariant_sum_equals_one(prices: list[float]) -> None:
    """El contrato sum(fair)==1 (sin re-normalizar) se cumple en todos los casos."""
    fair, _ = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
