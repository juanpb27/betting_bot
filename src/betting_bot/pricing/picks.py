"""Orchestrator: dado un evento + sus odds_snapshots + configs, genera `Pick`s.

Por cada mercado activo: filtra los snapshots, aplica `quality_gates`, de-vigga
las cuotas del sharp (Pinnacle) para obtener `p_real` por outcome, busca la mejor
cuota entre las comparison books, evalúa con `assess_value`, y construye un Pick
si hay valor real.

Devuelve `Pick` objects desconectados (sin `generated_at`/`generated_date` — los
pone `PickRepo.create` en Etapa 5).
"""
from __future__ import annotations

from betting_bot.persistence.models import Event, OddsSnapshot, Pick
from betting_bot.pricing.devigging import devig_multiplicative, devig_shin
from betting_bot.pricing.value import assess_value
from betting_bot.yaml_config import MarketConfig, QualityGates, StakingConfig


def generate_picks_for_event(
    *,
    event: Event,
    snapshots: list[OddsSnapshot],
    bankroll: int,
    markets: list[MarketConfig],
    sharp_ref_key: str,
    comparison_book_keys: frozenset[str],
    quality_gates: QualityGates,
    staking: StakingConfig,
) -> list[Pick]:
    """Genera todos los picks accionables para un evento, mercado por mercado."""
    # Sin bankroll no hay nada que stake-ear. Cortocircuito antes de gastar CPU.
    if bankroll <= 0:
        return []
    picks: list[Pick] = []
    for market in markets:
        market_snaps = [s for s in snapshots if s.market_key == market.key]
        picks.extend(
            _picks_for_market(
                event=event,
                market=market,
                snapshots=market_snaps,
                bankroll=bankroll,
                sharp_ref_key=sharp_ref_key,
                comparison_book_keys=comparison_book_keys,
                quality_gates=quality_gates,
                staking=staking,
            )
        )
    return picks


def _picks_for_market(
    *,
    event: Event,
    market: MarketConfig,
    snapshots: list[OddsSnapshot],
    bankroll: int,
    sharp_ref_key: str,
    comparison_book_keys: frozenset[str],
    quality_gates: QualityGates,
    staking: StakingConfig,
) -> list[Pick]:
    sharp_prices: dict[str, float] = {
        s.outcome: s.price for s in snapshots if s.bookmaker_key == sharp_ref_key
    }
    # Gate 1: el sharp debe cotizar TODOS los outcomes del mercado.
    if quality_gates.require_sharp_quoted and not all(
        o in sharp_prices for o in market.outcomes
    ):
        return []

    # Gate 2: mínimo de casas de comparación que cotizan este mercado.
    comp_books_present = {
        s.bookmaker_key for s in snapshots if s.bookmaker_key in comparison_book_keys
    }
    if len(comp_books_present) < quality_gates.min_comparison_books_quoted:
        return []

    # De-vigging del sharp → p_real por outcome.
    prices_in_order = [sharp_prices[o] for o in market.outcomes]
    sharp_overround: float | None = None  # solo Shin lo produce
    if market.devigging_method == "shin":
        try:
            fair_probs, sharp_overround = devig_shin(prices_in_order)
        except ValueError:
            # Solver no converge o invariante violado → no apostar este mercado.
            return []
    elif market.devigging_method == "multiplicative":
        fair_probs = devig_multiplicative(prices_in_order)
    else:
        # Bug de config (typo en markets.yaml): falla loud, no silenciosa.
        raise ValueError(
            f"Unknown devigging_method='{market.devigging_method}' "
            f"for market '{market.key}'"
        )

    picks: list[Pick] = []
    for i, outcome in enumerate(market.outcomes):
        comp_snaps = [
            s
            for s in snapshots
            if s.outcome == outcome and s.bookmaker_key in comparison_book_keys
        ]
        if not comp_snaps:
            continue
        # Tie-break determinista por bookmaker_key: si dos casas pagan exactamente
        # lo mismo, queremos el mismo Pick.comparison_book en cada corrida.
        best = max(comp_snaps, key=lambda s: (s.price, s.bookmaker_key))

        assessment = assess_value(
            fair_probs[i],
            best.price,
            bankroll=bankroll,
            min_ev=market.min_ev,
            kelly_divisor=staking.kelly_divisor,
            cap_pct=staking.cap_pct,
            floor_pct=staking.floor_pct,
            rounding_unit=staking.stake_rounding_unit,
        )
        if not assessment.has_value or assessment.stake_recommended == 0:
            continue

        picks.append(
            Pick(
                event_id=event.id,
                market_key=market.key,
                outcome=outcome,
                # h2h y btts no tienen línea; totals/spreads sí (TBD en etapa
                # posterior, cuando se agreguen al orchestrator).
                line=None,
                reference_book=sharp_ref_key,
                reference_price=sharp_prices[outcome],
                reference_prob=fair_probs[i],
                devigging_method=market.devigging_method,
                comparison_book=best.bookmaker_key,
                comparison_price=best.price,
                sharp_overround=sharp_overround,
                min_odds_for_value=assessment.min_odds_for_value,
                ev_at_comparison=assessment.ev,
                kelly_fraction=assessment.kelly_fraction,
                stake_recommended=assessment.stake_recommended,
                stake_pct_of_bankroll=(
                    assessment.stake_recommended / bankroll if bankroll > 0 else 0.0
                ),
                bankroll_at_generation=bankroll,
            )
        )

    return picks
