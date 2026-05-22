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
