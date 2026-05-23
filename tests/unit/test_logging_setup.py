"""Tests del bootstrap de structlog + propagación de request_id."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog

from betting_bot.logging_setup import bind_request_id, configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    """Cada test arranca con configuración limpia."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def test_configure_logging_is_idempotent() -> None:
    # Llamadas múltiples no deben romper. Útil cuando el bootstrap corre desde
    # más de un entry point en la misma corrida.
    configure_logging(level="INFO")
    configure_logging(level="DEBUG")


def test_bind_request_id_generates_uuid_when_none_passed() -> None:
    configure_logging(level="DEBUG")
    with bind_request_id() as rid:
        assert isinstance(rid, str)
        assert len(rid) >= 32  # UUID v4 con guiones tiene 36 chars


def test_bind_request_id_respects_explicit_value() -> None:
    configure_logging(level="DEBUG")
    with bind_request_id("manual-123") as rid:
        assert rid == "manual-123"


def test_bind_request_id_injects_into_contextvars() -> None:
    # Verificamos directamente sobre contextvars (capture_logs en structlog
    # 25.x no aplica el processor merge_contextvars por default; lo que vale
    # es que el valor esté en el storage que el processor real lee en prod).
    configure_logging(level="DEBUG")
    with bind_request_id("rid-test"):
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("request_id") == "rid-test"


def test_bind_request_id_clears_on_exit_even_after_exception() -> None:
    configure_logging(level="DEBUG")
    with pytest.raises(RuntimeError), bind_request_id("rid-error"):
        raise RuntimeError("boom")
    # Fuera del bloque, request_id NO debe quedar en contextvars.
    ctx = structlog.contextvars.get_contextvars()
    assert "request_id" not in ctx


def test_bind_request_id_inner_replaces_outer_then_inner_clears() -> None:
    # `unbind_contextvars` borra la key sin restaurar el valor exterior. Es
    # un trade-off conocido — los call sites del proyecto NO anidan request_id
    # (cada corrida o cada comando es su propio bloque top-level). Si en el
    # futuro hace falta restore, hay que pasar a `bind_contextvars` con tokens.
    configure_logging(level="DEBUG")
    with bind_request_id("outer"):
        assert structlog.contextvars.get_contextvars().get("request_id") == "outer"
        with bind_request_id("inner"):
            assert structlog.contextvars.get_contextvars().get("request_id") == "inner"
        # Al salir del inner, la key se borró — outer no se restaura.
        assert "request_id" not in structlog.contextvars.get_contextvars()
