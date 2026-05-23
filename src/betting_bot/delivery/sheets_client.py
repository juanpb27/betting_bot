"""Wrapper fino sobre gspread para append + bootstrap de hojas.

Diseño:
- Una sola conexión por proceso (`gspread.Client` se reusa).
- `ensure_worksheet` crea la hoja si no existe y le pone headers; si existe,
  es no-op (NO valida que los headers actuales coincidan — el operador o
  un check en Etapa 10 son responsables si hubo drift de schema).
- `append_row` delega a `worksheet.append_row` con `value_input_option=USER_ENTERED`
  (para que Sheets respete tipos numéricos vs strings, fechas, etc.).
- Errores de gspread (HTTPError, APIError) NO se silencian — el worker los
  captura para marcar `pending_sheets_sync.last_error` y reintentar.
"""
from __future__ import annotations

from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import ValueInputOption

# Scopes:
# - `spreadsheets` cubre lectura/escritura de cells y add_worksheet sobre un
#   spreadsheet ya compartido con el service account.
# - `drive.file` permite que el SA acceda a los archivos compartidos con él
#   (algunos casos lo exigen para list_worksheets cuando el sheet está
#   restringido). Lo incluimos por defecto para no quedar cortos.
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


class SheetsClient:
    """Cliente gspread con bootstrap de hojas y append seguro."""

    def __init__(self, *, spreadsheet_id: str, sa_path: str) -> None:
        # google-auth no anota from_service_account_file → ignore puntual.
        creds = Credentials.from_service_account_file(sa_path, scopes=_SCOPES)  # type: ignore[no-untyped-call]
        self._gc = gspread.authorize(creds)
        self._spreadsheet = self._gc.open_by_key(spreadsheet_id)
        # Cache de worksheets resueltas: evita repetir `worksheet()` lookup en
        # cada `append_row`. La worksheet API en gspread es local (sin
        # round-trip), pero igual ahorrar 1 call por fila importa cuando se
        # appendean 50+ filas por batch en el worker.
        self._cache: dict[str, gspread.Worksheet] = {}

    def ensure_worksheet(self, name: str, headers: list[str]) -> gspread.Worksheet:
        """Devuelve la worksheet `name`, creándola con `headers` si no existe.

        Idempotente. Si la hoja ya existe, NO sobrescribe headers existentes —
        asume que el operador (o el bootstrap previo) los puso bien. Validar
        que los headers actuales matcheen `headers` es deuda de Etapa 10.
        """
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        try:
            ws = self._spreadsheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(
                title=name, rows=1000, cols=len(headers)
            )
            ws.append_row(headers, value_input_option=ValueInputOption.user_entered)
        self._cache[name] = ws
        return ws

    def append_row(self, worksheet_name: str, row: list[Any]) -> None:
        """Agrega una fila al final de la hoja. La hoja debe haber sido
        creada con `ensure_worksheet` antes (o existir manualmente)."""
        ws = self._cache.get(worksheet_name)
        if ws is None:
            ws = self._spreadsheet.worksheet(worksheet_name)
            self._cache[worksheet_name] = ws
        ws.append_row(row, value_input_option=ValueInputOption.user_entered)
