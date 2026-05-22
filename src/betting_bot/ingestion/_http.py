"""Infraestructura HTTP compartida de los clientes de ingesta.

Contiene el helper de reintentos y el DTO `QuotaInfo` (lo que un request reporta
sobre el consumo de cuota de la API). Ambos clientes —the-odds-api y api-football—
lo usan.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

# Status codes que justifican reintentar: rate limit + errores transitorios de server.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class QuotaInfo:
    """Consumo de cuota de una API tras un request, para `api_quota_log`."""

    provider: str  # 'odds_api' | 'api_football'
    endpoint: str
    requests_remaining: int | None = None
    requests_used: int | None = None
    requests_limit: int | None = None


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
    **kwargs: Any,
) -> httpx.Response:
    """Hace un request reintentando ante errores transitorios.

    Reintenta ante `httpx.TransportError` (fallo de red) y status 429/5xx, con
    backoff exponencial. Agotados los intentos, propaga la última excepción o
    devuelve la última respuesta.
    """
    for attempt in range(1, max_attempts + 1):
        is_last = attempt == max_attempts
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            if is_last:
                raise
        else:
            if response.status_code not in _RETRYABLE_STATUS or is_last:
                return response
        await asyncio.sleep(backoff_seconds * 2 ** (attempt - 1))

    raise RuntimeError("request_with_retries: el loop terminó sin retornar")
