"""De-vigging: quita el margen de Pinnacle para obtener `p_real`.

Dos métodos:
- **Multiplicativo**: simple, proporcional. Para mercados de 2 vías (totals, btts,
  spreads).
- **Shin's method**: corrige el sesgo del favorito en mercados de 3 vías (h2h),
  resuelto con `scipy.optimize.brentq` sobre `[eps, 0.5 - eps]`.

Contrato: ambos devuelven probabilidades que SUMAN EXACTAMENTE 1 (tolerancia 1e-8).
NO se re-normaliza — si Shin no converge bien, falla fuerte; preferimos no apostar
antes que apostar con probs sesgadas en silencio.

Ref: H.S. Shin (1993), "Measuring the incidence of insider trading in a market for
state-contingent claims", Economic Journal 103, pp. 1141-1153.
"""
from __future__ import annotations

from scipy.optimize import brentq


def devig_multiplicative(prices: list[float]) -> list[float]:
    """De-vigging multiplicativo: remueve el margen proporcionalmente.

    Para mercados de 2 vías el sesgo del favorito es despreciable; este método
    alcanza y es muy estable.
    """
    implied = [1 / p for p in prices]
    total = sum(implied)
    return [p / total for p in implied]


def devig_shin(prices: list[float], tol: float = 1e-10) -> tuple[list[float], float]:
    """Shin's de-vigging vía root-finding con `brentq`.

    Devuelve `(probabilidades fair, z)` donde `z ∈ [0, 0.5)` es el parámetro de
    insider trading estimado. `z=0` significa mercado justo (sin overround).

    Lanza `ValueError` si el solver no bracketea la raíz o si el invariante
    `sum(fair) == 1` se viola — esos son fallos del modelo, no datos para
    persistir.
    """
    if len(prices) < 2:
        raise ValueError("Shin requires at least 2 outcomes")

    pi = [1 / p for p in prices]
    B = sum(pi)  # noqa: N806 — notación estándar de Shin (1993)

    # Mercado justo (sin overround): z=0 y las probs son las implícitas directas.
    if abs(B - 1.0) < tol:
        return pi, 0.0

    def fair_probs_given_z(z: float) -> list[float]:
        denominator = 2 * (1 - z)
        return [
            (((z**2) + 4 * (1 - z) * (p_i**2) / B) ** 0.5 - z) / denominator
            for p_i in pi
        ]

    def F(z: float) -> float:  # noqa: N802 — notación estándar (F(z)=0 es la raíz)
        return sum(fair_probs_given_z(z)) - 1.0

    eps = 1e-12
    try:
        z = brentq(F, eps, 0.5 - eps, xtol=tol, maxiter=200)
    except ValueError as exc:
        raise ValueError(
            f"Shin solver did not bracket a root for prices={prices}"
        ) from exc

    fair = fair_probs_given_z(float(z))

    # Invariante crítico: sum(fair) == 1.0 sin re-normalización.
    total = sum(fair)
    if abs(total - 1.0) > 1e-8:
        raise ValueError(
            f"Shin convergence invariant violated: sum(fair)={total}, "
            f"expected 1.0 (prices={prices}, z={z})"
        )

    return fair, float(z)
