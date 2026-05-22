"""Kelly fraccional + cálculo de stake.

Kelly fraccional = raw Kelly × `fraction` (típicamente 1/4). Cap en % del bankroll
para evitar concentración; floor para descartar edges minúsculos. Stake final en
unidades enteras de la moneda del deployment (`rounding_unit` desde
`bankroll.yaml`: 1000 COP, 1 USD, etc.).
"""
from __future__ import annotations


def kelly_fraction(p: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """Fracción del bankroll a apostar según Kelly fraccional.

    `fraction` es el divisor sobre full Kelly (0.25 = Kelly/4). Devuelve 0 si raw
    Kelly es ≤ 0 (no hay valor o exactamente breakeven), o si la cuota es
    degenerada (`decimal_odds <= 1.0`: el "premio neto" b sería ≤ 0).
    """
    if decimal_odds <= 1.0 or p <= 0.0:
        return 0.0
    b = decimal_odds - 1
    q = 1 - p
    raw_kelly = (b * p - q) / b
    if raw_kelly <= 0:
        return 0.0
    return raw_kelly * fraction


def calculate_stake(
    bankroll: float,
    p_real: float,
    decimal_odds: float,
    fraction: float = 0.25,
    cap: float = 0.03,
    floor: float = 0.003,
    rounding_unit: int = 1000,
) -> int:
    """Stake recomendado en unidades enteras de la moneda del deployment.

    Si la fracción de Kelly cae bajo `floor`, devuelve 0 (skip el pick). Si la
    supera `cap`, se trunca al cap. Redondea al múltiplo más cercano de
    `rounding_unit`.
    """
    f = kelly_fraction(p_real, decimal_odds, fraction)
    if f < floor:
        return 0
    f = min(f, cap)
    return round(bankroll * f / rounding_unit) * rounding_unit
