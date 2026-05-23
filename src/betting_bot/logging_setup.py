from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import structlog


def configure_logging(*, level: str = "INFO", json_output: bool | None = None) -> None:
    """Configura structlog + stdlib en una sola cadena de procesadores.
    """
    use_json = json_output if json_output is not None else os.getenv("LOG_JSON") == "1"

    # Procesadores compartidos: timestamp UTC, level, contextvars (request_id),
    # stack info en exceptions. Aplica a logs nuestros Y a los enrutados desde
    # stdlib via ProcessorFormatter.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str | None = None) -> Any:
    """Wrapper sobre `structlog.get_logger` para evitar import directo en el
    código de aplicación. Devuelve el bound logger ya configurado."""
    return structlog.get_logger(name)


@contextmanager
def bind_request_id(request_id: str | None = None) -> Iterator[str]:
    """Inyecta `request_id` en contextvars para toda log call del bloque.
    """
    rid = request_id or str(uuid4())
    structlog.contextvars.bind_contextvars(request_id=rid)
    try:
        yield rid
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
