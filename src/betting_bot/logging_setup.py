from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import structlog

# Patrones de credenciales que enmascaramos en cualquier log antes del render.
_SECRET_PATTERNS = [
    (re.compile(r"(apiKey=)[^&\s]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(/bot)\d+:[\w-]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(x-apisports-key:?\s*)[\w-]+", re.IGNORECASE), r"\1***REDACTED***"),
]


def _scrub_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Procesador structlog: enmascara API keys / bot tokens en cualquier
    string del event_dict antes del render.
    """
    for key, value in event_dict.items():
        if not isinstance(value, str):
            continue
        scrubbed = value
        for pattern, replacement in _SECRET_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)
        if scrubbed != value:
            event_dict[key] = scrubbed
    return event_dict


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
        _scrub_secrets,
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
