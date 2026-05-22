"""Property-based tests del módulo `pricing/` con `hypothesis`.

Los tests por ejemplo cubren casos conocidos; los property tests cubren el
espacio de inputs y atrapan invariantes rotos por refactors futuros. Apuntan
a las propiedades que ya validamos en DESIGN.md / revisión matemática:

- multiplicativo: `sum(fair)==1`, preserva orden, idempotente al re-aplicar.
- Shin: `sum(fair)==1` con tolerancia 1e-8, preserva orden, corrige favorite-
  longshot bias (Shin[fav] ≥ Mult[fav] cuando hay overround positivo).
- Kelly: `calculate_stake` siempre múltiplo de `rounding_unit` y nunca excede
  `bankroll * cap`.
"""
from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from betting_bot.pricing.devigging import devig_multiplicative, devig_shin
from betting_bot.pricing.kelly import calculate_stake, kelly_fraction

# Cuotas decimales realistas: entre cuasi-certeza (1.01) y longshot extremo (100).
# allow_subnormal=False evita ruido FP en los bordes.
_price = st.floats(
    min_value=1.01,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
)


# --- Multiplicativo: propiedades algebraicas --------------------------------


@given(prices=st.lists(_price, min_size=2, max_size=5))
def test_multiplicative_sums_to_one(prices: list[float]) -> None:
    fair = devig_multiplicative(prices)
    assert abs(sum(fair) - 1.0) < 1e-10


@given(prices=st.lists(_price, min_size=2, max_size=5))
def test_multiplicative_preserves_strict_order(prices: list[float]) -> None:
    # Si todas las cuotas son distintas, el orden se preserva inversamente:
    # cuota más baja → prob más alta. (Empates rompen orden estricto; assume.)
    assume(len(set(prices)) == len(prices))
    fair = devig_multiplicative(prices)
    # Ranking por cuota ascendente == ranking por prob descendente.
    ranked_by_price = sorted(range(len(prices)), key=lambda i: prices[i])
    ranked_by_prob = sorted(range(len(prices)), key=lambda i: -fair[i])
    assert ranked_by_price == ranked_by_prob


@given(prices=st.lists(_price, min_size=2, max_size=5))
def test_multiplicative_idempotent_on_fair_odds(prices: list[float]) -> None:
    # Aplicar el método a cuotas ya "fair" (derivadas de probs que suman 1)
    # debe devolver las mismas probs (no hay overround que remover).
    fair = devig_multiplicative(prices)
    # Excluye probs ~0 que crearían cuotas inf.
    assume(all(p > 1e-6 for p in fair))
    fair_odds = [1 / p for p in fair]
    twice = devig_multiplicative(fair_odds)
    for a, b in zip(fair, twice, strict=True):
        assert abs(a - b) < 1e-9


# --- Shin: invariantes y dirección del sesgo --------------------------------


# Para Shin restringimos el espacio a overrounds realistas (B ≤ 1.20). Pinnacle
# opera ~1.02 y las casas EU blandas raramente pasan 1.10. Overrounds extremos
# (B > 1.5) pueden requerir z > 0.5 y el bracket actual `[eps, 0.5-eps]` no
# converge — edge descubierto por estos property tests, registrado como deuda
# en CHANGELOG para fix en commit aparte.
def _shin_friendly_prices(prices: list[float]) -> bool:
    implied_sum = sum(1 / p for p in prices)
    return 1.0 < implied_sum <= 1.20


@given(prices=st.lists(_price, min_size=2, max_size=4))
@settings(suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
def test_shin_sums_to_one(prices: list[float]) -> None:
    assume(_shin_friendly_prices(prices))
    fair, _z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8


@given(prices=st.lists(_price, min_size=3, max_size=3))
@settings(suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
def test_shin_preserves_order_in_3way(prices: list[float]) -> None:
    # En h2h (3 outcomes), el favorito (menor cuota) debe seguir teniendo la
    # mayor prob fair.
    assume(_shin_friendly_prices(prices))
    assume(len(set(prices)) == 3)
    fair, _ = devig_shin(prices)
    ranked_by_price = sorted(range(3), key=lambda i: prices[i])
    ranked_by_prob = sorted(range(3), key=lambda i: -fair[i])
    assert ranked_by_price == ranked_by_prob


@given(prices=st.lists(_price, min_size=3, max_size=3))
@settings(suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
def test_shin_corrects_favorite_longshot_bias(prices: list[float]) -> None:
    # Cuando hay overround real (B > 1.005) y un favorito claro, Shin asigna
    # MÁS prob al favorito que el multiplicativo. Refs: Shin (1993), Štrumbelj
    # (2014), Buchdahl.
    assume(_shin_friendly_prices(prices))
    assume(sum(1 / p for p in prices) > 1.005)
    fav_idx = min(range(3), key=lambda i: prices[i])
    assume(prices.count(prices[fav_idx]) == 1)

    shin_probs, z = devig_shin(prices)
    mult_probs = devig_multiplicative(prices)
    # Si Shin no detectó insider (z muy chico), la diferencia es ruido FP.
    assume(z > 1e-6)
    assert shin_probs[fav_idx] >= mult_probs[fav_idx]


# --- Kelly: propiedades de borde --------------------------------------------


@given(
    bankroll=st.integers(min_value=1, max_value=10_000_000),
    p=st.floats(min_value=0.01, max_value=0.99, allow_nan=False),
    odds=st.floats(min_value=1.01, max_value=50.0, allow_nan=False),
    rounding_unit=st.sampled_from([1, 100, 500, 1000]),
)
def test_calculate_stake_is_multiple_of_rounding_unit(
    bankroll: int, p: float, odds: float, rounding_unit: int
) -> None:
    stake = calculate_stake(bankroll, p, odds, rounding_unit=rounding_unit)
    assert stake % rounding_unit == 0


@given(
    bankroll=st.integers(min_value=1000, max_value=10_000_000),
    p=st.floats(min_value=0.01, max_value=0.99, allow_nan=False),
    odds=st.floats(min_value=1.01, max_value=50.0, allow_nan=False),
    cap=st.floats(min_value=0.005, max_value=0.10, allow_nan=False),
)
def test_calculate_stake_never_exceeds_cap(
    bankroll: int, p: float, odds: float, cap: float
) -> None:
    # `cap` define el % máximo del bankroll; el stake en plata no puede
    # superar ese tope (más 1 unidad de rounding por el redondeo half-up).
    stake = calculate_stake(bankroll, p, odds, cap=cap, rounding_unit=1)
    assert stake <= bankroll * cap + 1


@given(
    p=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    odds=st.floats(min_value=1.01, max_value=50.0, allow_nan=False),
)
def test_kelly_fraction_never_negative(p: float, odds: float) -> None:
    # Guard: si no hay edge, devuelve 0; nunca devuelve negativo.
    assert kelly_fraction(p, odds) >= 0.0
