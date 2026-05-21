from __future__ import annotations

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
