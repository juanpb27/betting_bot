from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from betting_bot.config import get_settings


def load_yaml(filename: str) -> dict[str, Any]:
    """Carga un archivo YAML y devuelve su contenido como dict."""
    path = get_settings().config_dir / filename
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{filename}: se esperaba un mapping en la raíz del YAML")
    return data


@lru_cache(maxsize=1)
def load_book_codes() -> frozenset[str]:
    """Códigos de las casas destino."""
    data = load_yaml("books.yaml")
    books = data.get("destination_books", [])
    codes = {book["code"] for book in books}
    if not codes:
        raise ValueError("books.yaml: no hay 'destination_books' definidas")
    return frozenset(codes)


@dataclass(frozen=True)
class LeagueConfig:
    """Una liga activa: su key en the-odds-api y su id en api-football."""

    key: str
    api_football_id: int


def load_active_leagues() -> list[LeagueConfig]:
    """Ligas con `active: true` en config/leagues.yaml."""
    data = load_yaml("leagues.yaml")
    return [
        LeagueConfig(key=lg["key"], api_football_id=lg["api_football_id"])
        for lg in data.get("leagues", [])
        if lg.get("active", False)
    ]


def load_odds_bookmakers() -> list[str]:
    """Casas a pedir a the-odds-api: la sharp (Pinnacle) + las de comparación."""
    data = load_yaml("books.yaml")
    sharp = data["sharp_reference"]["key"]
    comparison = [
        book["key"]
        for book in data.get("comparison_books", [])
        if book.get("enabled", True)
    ]
    return [sharp, *comparison]


# --- Configs para pricing (Etapa 4) -------------------------------------------


@dataclass(frozen=True)
class MarketConfig:
    """Un mercado activo: identificador, método de de-vigging y umbral de EV."""

    key: str
    name: str
    outcomes: list[str]
    devigging_method: str  # "shin" | "multiplicative"
    min_ev: float


@dataclass(frozen=True)
class StakingConfig:
    """Parámetros de staking (Kelly fraccional) desde bankroll.yaml."""

    kelly_divisor: float
    cap_pct: float
    floor_pct: float
    stake_rounding_unit: int


@dataclass(frozen=True)
class QualityGates:
    """Gates de calidad para decidir si vale la pena evaluar un mercado."""

    require_sharp_quoted: bool
    min_comparison_books_quoted: int


def load_active_markets() -> list[MarketConfig]:
    """Mercados con `enabled: true` en config/markets.yaml."""
    data = load_yaml("markets.yaml")
    return [
        MarketConfig(
            key=m["key"],
            name=m["name"],
            outcomes=list(m["outcomes"]),
            devigging_method=m["devigging_method"],
            min_ev=float(m["min_ev"]),
        )
        for m in data.get("markets", [])
        if m.get("enabled", False)
    ]


def load_staking_config() -> StakingConfig:
    """Parámetros de Kelly y redondeo desde config/bankroll.yaml."""
    data = load_yaml("bankroll.yaml")
    staking = data["staking"]
    return StakingConfig(
        kelly_divisor=float(staking["kelly_divisor"]),
        cap_pct=float(staking["cap_pct"]),
        floor_pct=float(staking["floor_pct"]),
        stake_rounding_unit=int(staking["stake_rounding_unit"]),
    )


def load_quality_gates() -> QualityGates:
    """Gates desde config/thresholds.yaml (sección `quality_gates`)."""
    data = load_yaml("thresholds.yaml")
    qg = data["quality_gates"]
    return QualityGates(
        require_sharp_quoted=bool(qg["require_sharp_quoted"]),
        min_comparison_books_quoted=int(qg["min_comparison_books_quoted"]),
    )


def load_sharp_reference_key() -> str:
    """Key de la casa sharp (Pinnacle) usada para calcular `p_real`."""
    data = load_yaml("books.yaml")
    return str(data["sharp_reference"]["key"])


def load_comparison_book_keys() -> frozenset[str]:
    """Keys de las casas de comparación habilitadas (sin la sharp)."""
    data = load_yaml("books.yaml")
    return frozenset(
        book["key"]
        for book in data.get("comparison_books", [])
        if book.get("enabled", True)
    )
