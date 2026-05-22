"""Tests del normalizer/matcher de eventos (TDD).

Cruzar mal dos eventos = generar un pick sobre el partido equivocado. Lógica core.
"""
from __future__ import annotations

from datetime import UTC, datetime

from betting_bot.ingestion.normalizer import match_events, normalize_team_name
from tests.factories import build_fixture, build_odds_event

_WHEN = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)


# --- normalize_team_name ------------------------------------------------------


def test_normalize_strips_accents_and_club_suffix() -> None:
    assert normalize_team_name("Arsenal FC") == "arsenal"
    assert normalize_team_name("Atlético Madrid") == "atletico madrid"
    assert normalize_team_name("  Real Madrid CF ") == "real madrid"


def test_normalize_leaves_names_without_suffix() -> None:
    assert normalize_team_name("Brighton & Hove Albion") == "brighton & hove albion"


# --- match_events -------------------------------------------------------------


def test_match_events_finds_the_matching_fixture() -> None:
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    fixtures = [
        build_fixture(home_team="Brighton", away_team="Everton", date=_WHEN, fixture_id=1),
        build_fixture(home_team="Arsenal", away_team="Chelsea", date=_WHEN, fixture_id=2),
    ]
    match = match_events(odds, fixtures)
    assert match is not None
    assert match.fixture.fixture.id == 2
    assert match.confidence >= 90


def test_match_events_matches_despite_accents_and_suffix() -> None:
    odds = build_odds_event(
        home_team="Atletico Madrid", away_team="Sevilla FC", commence_time=_WHEN
    )
    fixture = build_fixture(
        home_team="Atlético Madrid", away_team="Sevilla", date=_WHEN, fixture_id=7
    )
    match = match_events(odds, [fixture])
    assert match is not None
    assert match.fixture.fixture.id == 7


def test_match_events_skips_fixture_outside_time_window() -> None:
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    # Mismos equipos pero 9h después → fuera de la ventana de 6h.
    far = build_fixture(
        home_team="Arsenal",
        away_team="Chelsea",
        date=datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
        fixture_id=9,
    )
    assert match_events(odds, [far]) is None


def test_match_events_circuit_breaker_below_threshold() -> None:
    # Equipos completamente distintos → confianza < 90 → None (se saltea el evento).
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    wrong = build_fixture(
        home_team="Liverpool", away_team="Tottenham", date=_WHEN, fixture_id=3
    )
    assert match_events(odds, [wrong]) is None


def test_match_events_picks_highest_confidence() -> None:
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    fixtures = [
        build_fixture(home_team="Arsenal", away_team="Chelsa", date=_WHEN, fixture_id=1),
        build_fixture(home_team="Arsenal", away_team="Chelsea", date=_WHEN, fixture_id=2),
    ]
    match = match_events(odds, fixtures)
    assert match is not None
    assert match.fixture.fixture.id == 2  # el match exacto gana


def test_match_events_returns_none_when_no_candidates() -> None:
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    assert match_events(odds, []) is None


def test_match_events_returns_first_of_equal_confidence() -> None:
    # Dos fixtures idénticos → misma confianza; gana el primero (comparación `>`).
    odds = build_odds_event(home_team="Arsenal", away_team="Chelsea", commence_time=_WHEN)
    fixtures = [
        build_fixture(home_team="Arsenal", away_team="Chelsea", date=_WHEN, fixture_id=1),
        build_fixture(home_team="Arsenal", away_team="Chelsea", date=_WHEN, fixture_id=2),
    ]
    match = match_events(odds, fixtures)
    assert match is not None
    assert match.fixture.fixture.id == 1
