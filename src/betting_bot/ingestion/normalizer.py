"""Matcher de eventos: cruza un evento de the-odds-api con su fixture de api-football.

Las dos APIs no comparten identificadores: una usa nombres de equipo string, la
otra IDs enteros. El cruce se hace por similitud difusa de nombres + validación
de horario. Si la confianza no alcanza el umbral, el evento se saltea (circuit
breaker) — preferimos no apostar antes que apostar sobre el partido equivocado.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from betting_bot.ingestion.schemas import ApiFootballFixture, OddsApiEvent

# Sufijos de club que se quitan para comparar ("Arsenal FC" ~ "Arsenal").
_TEAM_SUFFIXES = (" fc", " cf", " sc", " ac", " afc")


def normalize_team_name(name: str) -> str:
    """Normaliza un nombre de equipo para comparación difusa.

    Descompone Unicode (NFKD) y descarta los diacríticos, pasa a minúsculas,
    recorta espacios y quita un sufijo de club si lo hay.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = ascii_name.lower().strip()
    for suffix in _TEAM_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    return cleaned


@dataclass(frozen=True)
class MatchCandidate:
    """Resultado de un match: el fixture de api-football y su confianza [0, 100]."""

    fixture: ApiFootballFixture
    confidence: float


def match_events(
    odds_event: OddsApiEvent,
    fixtures: list[ApiFootballFixture],
    *,
    min_confidence: float = 90.0,
    time_window_hours: float = 6.0,
) -> MatchCandidate | None:
    """Encuentra el fixture de api-football que corresponde a `odds_event`.

    Descarta candidatos cuyo horario de inicio difiera más de `time_window_hours`.
    La confianza es el promedio del `token_sort_ratio` de los nombres home y away.
    Devuelve el mejor candidato con confianza >= `min_confidence`, o `None` si
    ninguno alcanza el umbral.
    """
    odds_home = normalize_team_name(odds_event.home_team)
    odds_away = normalize_team_name(odds_event.away_team)
    window_seconds = time_window_hours * 3600

    best: MatchCandidate | None = None
    for fixture in fixtures:
        delta = abs(
            (odds_event.commence_time - fixture.fixture.date).total_seconds()
        )
        if delta > window_seconds:
            continue

        score_home = fuzz.token_sort_ratio(
            odds_home, normalize_team_name(fixture.teams.home.name)
        )
        score_away = fuzz.token_sort_ratio(
            odds_away, normalize_team_name(fixture.teams.away.name)
        )
        confidence = (score_home + score_away) / 2

        if confidence >= min_confidence and (
            best is None or confidence > best.confidence
        ):
            best = MatchCandidate(fixture=fixture, confidence=confidence)

    return best
