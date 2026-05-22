"""Tests de los loaders de YAML — corren contra el `config/` real del repo."""
from __future__ import annotations

from betting_bot.yaml_config import (
    load_active_leagues,
    load_active_markets,
    load_book_codes,
    load_comparison_book_keys,
    load_odds_bookmakers,
    load_quality_gates,
    load_sharp_reference_key,
    load_staking_config,
)


def test_load_book_codes_has_destination_books() -> None:
    codes = load_book_codes()
    assert {"betplay", "codere", "rushbet", "bwin"} <= codes


def test_load_active_leagues_returns_only_active() -> None:
    leagues = load_active_leagues()
    keys = {lg.key for lg in leagues}
    assert "soccer_epl" in keys
    # World Cup está en active: false → no debería aparecer.
    assert "soccer_fifa_world_cup" not in keys


def test_load_odds_bookmakers_includes_pinnacle_first() -> None:
    books = load_odds_bookmakers()
    assert books[0] == "pinnacle"
    assert "bet365" in books


def test_load_active_markets_includes_h2h_with_shin() -> None:
    markets = load_active_markets()
    h2h = next(m for m in markets if m.key == "h2h")
    assert h2h.devigging_method == "shin"
    assert h2h.min_ev == 0.03
    assert h2h.outcomes == ["home", "draw", "away"]


def test_load_staking_config_matches_bankroll_yaml() -> None:
    cfg = load_staking_config()
    assert cfg.kelly_divisor == 4.0
    assert cfg.cap_pct == 0.03
    assert cfg.floor_pct == 0.003
    assert cfg.stake_rounding_unit == 1000


def test_load_quality_gates() -> None:
    qg = load_quality_gates()
    assert qg.require_sharp_quoted is True
    assert qg.min_comparison_books_quoted == 2


def test_load_sharp_reference_key() -> None:
    assert load_sharp_reference_key() == "pinnacle"


def test_load_comparison_book_keys() -> None:
    keys = load_comparison_book_keys()
    assert "bet365" in keys
    assert "betsson" in keys
    # Pinnacle no debe estar acá (es la sharp, no comparison).
    assert "pinnacle" not in keys
