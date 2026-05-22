"""Cálculo de EV y `assess_value`: el predicado de valor.

Hay valor cuando `EV = p_real*(odds-1) - (1-p_real) >= min_ev`. Si hay valor, se
calcula el stake con Kelly fraccional. Si no, los campos de stake quedan en 0
(pero el `ValueAssessment` igual se devuelve con `ev`, `min_odds_for_value`,
etc. para que el llamador pueda registrar por qué no apostó).
"""
from __future__ import annotations

from dataclasses import dataclass

from betting_bot.pricing.kelly import calculate_stake, kelly_fraction


@dataclass(frozen=True)
class ValueAssessment:
    """Resultado de evaluar una cuota contra una probabilidad real."""

    p_real: float
    odds: float
    ev: float
    # Cuota mínima que recién dispara has_value=True dado min_ev:
    #   (1 + min_ev) / p_real. Para min_ev=0 colapsa al breakeven 1/p_real.
    min_odds_for_value: float
    kelly_fraction: float
    stake_recommended: int  # 0 si has_value=False o si kelly_frac < floor
    has_value: bool


def assess_value(
    p_real: float,
    odds: float,
    bankroll: float,
    min_ev: float = 0.025,
    kelly_divisor: float = 4.0,
    cap_pct: float = 0.03,
    floor_pct: float = 0.003,
    rounding_unit: int = 1000,
) -> ValueAssessment:
    """Evalúa si una cuota tiene valor positivo y calcula el stake recomendado.
    """
    if p_real <= 0.0:
        return ValueAssessment(
            p_real=p_real,
            odds=odds,
            ev=-1.0,
            min_odds_for_value=float("inf"),
            kelly_fraction=0.0,
            stake_recommended=0,
            has_value=False,
        )

    ev = p_real * (odds - 1) - (1 - p_real)
    # Cuota mínima que produce EV == min_ev: (1 + min_ev) / p_real.
    min_odds = (1 + min_ev) / p_real

    if ev < min_ev:
        return ValueAssessment(
            p_real=p_real,
            odds=odds,
            ev=ev,
            min_odds_for_value=min_odds,
            kelly_fraction=0.0,
            stake_recommended=0,
            has_value=False,
        )

    fraction = 1 / kelly_divisor
    kelly_frac = kelly_fraction(p_real, odds, fraction=fraction)
    stake = calculate_stake(
        bankroll,
        p_real,
        odds,
        fraction=fraction,
        cap=cap_pct,
        floor=floor_pct,
        rounding_unit=rounding_unit,
    )

    return ValueAssessment(
        p_real=p_real,
        odds=odds,
        ev=ev,
        min_odds_for_value=min_odds,
        kelly_fraction=kelly_frac,
        stake_recommended=stake,
        has_value=True,
    )
