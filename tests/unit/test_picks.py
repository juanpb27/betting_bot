"""Tests del orchestrator `generate_picks_for_event`.

El orchestrator toma odds_snapshots + configs, aplica de-vigging, busca la mejor
cuota EU contra `p_real`, gatea con `quality_gates` + `min_ev`, y devuelve
objetos `Pick` desconectados (sin `generated_at`/`generated_date` — los pone
`PickRepo.create` en Etapa 5).
"""
from __future__ import annotations

import pytest

from betting_bot.persistence.models import Event, OddsSnapshot, Pick
from betting_bot.pricing.picks import generate_picks_for_event
from betting_bot.yaml_config import MarketConfig, QualityGates, StakingConfig
from tests.factories import build_event, build_odds_snapshot

_H2H = MarketConfig(
    key="h2h",
    name="1X2",
    outcomes=["home", "draw", "away"],
    devigging_method="shin",
    min_ev=0.03,
)
_STAKING = StakingConfig(
    kelly_divisor=4.0, cap_pct=0.03, floor_pct=0.003, stake_rounding_unit=1000
)
_GATES = QualityGates(require_sharp_quoted=True, min_comparison_books_quoted=2)
_SHARP = "pinnacle"
_COMPS: frozenset[str] = frozenset({"bet365", "betsson"})


def _h2h_snaps(
    event_id: str,
    sharp_prices: dict[str, float],
    comparison_prices: dict[tuple[str, str], float],
) -> list[OddsSnapshot]:
    """Construye snapshots h2h. `comparison_prices` clave = (book, outcome)."""
    snaps: list[OddsSnapshot] = []
    for outcome, price in sharp_prices.items():
        snaps.append(
            build_odds_snapshot(
                event_id=event_id,
                bookmaker_key=_SHARP,
                outcome=outcome,
                price=price,
            )
        )
    for (book, outcome), price in comparison_prices.items():
        snaps.append(
            build_odds_snapshot(
                event_id=event_id,
                bookmaker_key=book,
                outcome=outcome,
                price=price,
            )
        )
    return snaps


def _generate(
    snapshots: list[OddsSnapshot],
    event: Event,
    *,
    gates: QualityGates = _GATES,
    bankroll: int = 1_000_000,
) -> list[Pick]:
    return generate_picks_for_event(
        event=event,
        snapshots=snapshots,
        bankroll=bankroll,
        markets=[_H2H],
        sharp_ref_key=_SHARP,
        comparison_book_keys=_COMPS,
        quality_gates=gates,
        staking=_STAKING,
    )


# --- Happy path ---------------------------------------------------------------


def test_generates_pick_when_ev_above_min() -> None:
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        # Pinnacle ~ fair: p_home_real ≈ 0.50.
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.30,  # EV alto: 0.50 * 1.30 - 0.50 ≈ 0.15
            ("bet365", "draw"): 3.50,
            ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.20,
            ("betsson", "draw"): 3.45,
            ("betsson", "away"): 3.90,
        },
    )
    picks = generate_picks_for_event(
        event=event,
        snapshots=snaps,
        bankroll=1_000_000,
        markets=[_H2H],
        sharp_ref_key=_SHARP,
        comparison_book_keys=_COMPS,
        quality_gates=_GATES,
        staking=_STAKING,
    )
    home_picks = [p for p in picks if p.outcome == "home"]
    assert len(home_picks) == 1
    home = home_picks[0]
    assert home.event_id == event.id
    assert home.market_key == "h2h"
    assert home.devigging_method == "shin"
    assert home.reference_book == "pinnacle"
    assert home.reference_price == 1.95
    # La mejor cuota de comparación gana (bet365=2.30 > betsson=2.20).
    assert home.comparison_book == "bet365"
    assert home.comparison_price == 2.30
    assert home.stake_recommended > 0
    assert home.bankroll_at_generation == 1_000_000
    assert home.line is None


# --- Quality gates ------------------------------------------------------------


def test_no_picks_when_sharp_not_quoted_and_required() -> None:
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={},  # sin Pinnacle
        comparison_prices={
            ("bet365", "home"): 2.30,
            ("bet365", "draw"): 3.50,
            ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.20,
            ("betsson", "draw"): 3.45,
            ("betsson", "away"): 3.90,
        },
    )
    picks = _generate(snaps, event)
    assert picks == []


def test_no_picks_when_below_min_comparison_books() -> None:
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            # Solo una casa de comparación → < min_comparison_books_quoted=2.
            ("bet365", "home"): 2.30,
            ("bet365", "draw"): 3.50,
            ("bet365", "away"): 4.00,
        },
    )
    picks = _generate(snaps, event)
    assert picks == []


def test_no_picks_when_ev_below_min_ev() -> None:
    # Sharp y comparación casi iguales → EV cerca de cero → < min_ev=0.03.
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 1.95,
            ("bet365", "draw"): 3.55,
            ("bet365", "away"): 4.05,
            ("betsson", "home"): 1.94,
            ("betsson", "draw"): 3.50,
            ("betsson", "away"): 4.00,
        },
    )
    picks = _generate(snaps, event)
    assert picks == []


# --- Selección de la mejor cuota de comparación -------------------------------


def test_selects_highest_comparison_price() -> None:
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.10,
            ("bet365", "draw"): 3.50,
            ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.40,  # mejor cuota home
            ("betsson", "draw"): 3.55,
            ("betsson", "away"): 4.05,
        },
    )
    picks = _generate(snaps, event)
    home = next(p for p in picks if p.outcome == "home")
    assert home.comparison_book == "betsson"
    assert home.comparison_price == 2.40


# --- Otras ramas del orchestrator --------------------------------------------


def test_skips_outcome_without_comparison_quotes() -> None:
    # Sharp quota los 3 outcomes, pero las casas EU solo cotizan "home".
    # → solo "home" se evalúa; "draw" y "away" se saltean en silencio.
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.30,
            ("betsson", "home"): 2.25,
        },
    )
    picks = _generate(snaps, event)
    assert all(p.outcome == "home" for p in picks)


def test_uses_multiplicative_method_when_market_says_so() -> None:
    # Mercado de 2 vías con método multiplicativo (totals).
    totals_market = MarketConfig(
        key="totals",
        name="Over/Under",
        outcomes=["over", "under"],
        devigging_method="multiplicative",
        min_ev=0.025,
    )
    event = build_event()
    snaps = [
        build_odds_snapshot(
            event_id=event.id, bookmaker_key=_SHARP,
            market_key="totals", outcome="over", price=1.90,
        ),
        build_odds_snapshot(
            event_id=event.id, bookmaker_key=_SHARP,
            market_key="totals", outcome="under", price=1.90,
        ),
        # Comparación paga 2.10 sobre over → EV ≈ 0.05, holgadamente sobre min_ev.
        build_odds_snapshot(
            event_id=event.id, bookmaker_key="bet365",
            market_key="totals", outcome="over", price=2.10,
        ),
        build_odds_snapshot(
            event_id=event.id, bookmaker_key="bet365",
            market_key="totals", outcome="under", price=1.85,
        ),
        build_odds_snapshot(
            event_id=event.id, bookmaker_key="betsson",
            market_key="totals", outcome="over", price=2.00,
        ),
        build_odds_snapshot(
            event_id=event.id, bookmaker_key="betsson",
            market_key="totals", outcome="under", price=1.85,
        ),
    ]
    picks = generate_picks_for_event(
        event=event, snapshots=snaps, bankroll=1_000_000,
        markets=[totals_market], sharp_ref_key=_SHARP,
        comparison_book_keys=_COMPS, quality_gates=_GATES, staking=_STAKING,
    )
    over_picks = [p for p in picks if p.outcome == "over"]
    assert len(over_picks) == 1
    assert over_picks[0].devigging_method == "multiplicative"


def test_raises_for_unknown_devigging_method() -> None:
    # `devigging_method` desconocido = bug de config (typo en markets.yaml).
    # Debe fallar loud, no silenciosa — un mercado mal configurado nunca
    # generaría picks y nadie se enteraría.
    weird = MarketConfig(
        key="h2h", name="x", outcomes=["home", "draw", "away"],
        devigging_method="not_a_method", min_ev=0.03,
    )
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.30, ("bet365", "draw"): 3.50, ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.20, ("betsson", "draw"): 3.45, ("betsson", "away"): 3.90,
        },
    )
    with pytest.raises(ValueError, match="Unknown devigging_method"):
        generate_picks_for_event(
            event=event, snapshots=snaps, bankroll=1_000_000,
            markets=[weird], sharp_ref_key=_SHARP,
            comparison_book_keys=_COMPS, quality_gates=_GATES, staking=_STAKING,
        )


def test_returns_empty_when_bankroll_is_zero_or_negative() -> None:
    # Sin bankroll no hay nada que stakear: cortocircuito sin evaluar mercados.
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.30, ("bet365", "draw"): 3.50, ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.20, ("betsson", "draw"): 3.45, ("betsson", "away"): 3.90,
        },
    )
    assert _generate(snaps, event, bankroll=0) == []
    assert _generate(snaps, event, bankroll=-100) == []


def test_best_comparison_tiebreak_is_deterministic() -> None:
    # Dos casas con la MISMA cuota: el tie-break debe ser determinista —
    # el mismo input (en cualquier orden) debe producir el mismo Pick.comparison_book.
    # La regla exacta es "max alfabético del bookmaker_key" pero lo que importa
    # auditablemente es que NO dependa del orden de iteración del input.
    event = build_event()
    snaps = _h2h_snaps(
        event.id,
        sharp_prices={"home": 1.95, "draw": 3.60, "away": 4.10},
        comparison_prices={
            ("bet365", "home"): 2.30,
            ("bet365", "draw"): 3.50,
            ("bet365", "away"): 4.00,
            ("betsson", "home"): 2.30,  # ← empate exacto con bet365 en "home"
            ("betsson", "draw"): 3.45,
            ("betsson", "away"): 3.90,
        },
    )
    picks_a = _generate(snaps, event)
    picks_b = _generate(list(reversed(snaps)), event)
    home_a = next(p for p in picks_a if p.outcome == "home")
    home_b = next(p for p in picks_b if p.outcome == "home")
    assert home_a.comparison_book == home_b.comparison_book
    # Y, concretamente, max((price, book_key)) gana el último alfabético: "betsson".
    assert home_a.comparison_book == "betsson"
