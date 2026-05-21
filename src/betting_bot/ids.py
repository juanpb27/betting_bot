from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    """Genera un identificador único para entidades cross-system (events, picks).
    """
    return str(uuid4())
