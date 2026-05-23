"""Smoke tests del wrapper gspread.

NO toca Google. Mockea `gspread.authorize` y `Credentials.from_service_account_file`.
Validamos lógica de bootstrap (create vs no-op), cache de worksheets, y
propagación de errores HTTP/API (contrato crítico para el worker outbox).
Integration real va en Etapa 10.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import gspread
import pytest
from gspread.utils import ValueInputOption

from betting_bot.delivery.sheets_client import SheetsClient


@pytest.fixture
def fake_spreadsheet() -> MagicMock:
    sp = MagicMock(name="spreadsheet")
    return sp


@pytest.fixture
def client(fake_spreadsheet: MagicMock) -> SheetsClient:
    with patch(
        "betting_bot.delivery.sheets_client.Credentials.from_service_account_file",
        return_value=MagicMock(),
    ), patch(
        "betting_bot.delivery.sheets_client.gspread.authorize"
    ) as auth:
        gc = MagicMock()
        gc.open_by_key.return_value = fake_spreadsheet
        auth.return_value = gc
        return SheetsClient(spreadsheet_id="sid", sa_path="/tmp/fake.json")


# --- ensure_worksheet -------------------------------------------------------


def test_ensure_worksheet_creates_with_headers_if_missing(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    fake_spreadsheet.worksheet.side_effect = gspread.exceptions.WorksheetNotFound
    created = MagicMock(name="new_ws")
    fake_spreadsheet.add_worksheet.return_value = created

    client.ensure_worksheet("Picks", ["A", "B", "C"])

    fake_spreadsheet.add_worksheet.assert_called_once_with(
        title="Picks", rows=1000, cols=3
    )
    created.append_row.assert_called_once_with(
        ["A", "B", "C"], value_input_option=ValueInputOption.user_entered
    )


def test_ensure_worksheet_noop_if_exists(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    existing = MagicMock(name="existing_ws")
    fake_spreadsheet.worksheet.return_value = existing

    client.ensure_worksheet("Picks", ["A", "B"])

    fake_spreadsheet.add_worksheet.assert_not_called()
    # NO sobrescribe headers existentes.
    existing.append_row.assert_not_called()


def test_ensure_worksheet_uses_cache_avoiding_repeat_lookup(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    """El cache debe evitar volver a llamar spreadsheet.worksheet() para la
    misma hoja. Si el cache se rompe, este test detecta más de 1 lookup."""
    existing = MagicMock(name="ws")
    fake_spreadsheet.worksheet.return_value = existing

    client.ensure_worksheet("Picks", ["A"])
    client.ensure_worksheet("Picks", ["A"])
    client.ensure_worksheet("Picks", ["A"])

    # Solo 1 lookup en total: la primera ensure. Las otras dos vienen del cache.
    assert fake_spreadsheet.worksheet.call_count == 1


# --- append_row -------------------------------------------------------------


def test_append_row_uses_cache_when_previously_ensured(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    """Si la hoja fue resuelta vía ensure_worksheet, append_row reusa el cache
    en vez de hacer otro lookup."""
    existing = MagicMock(name="ws")
    fake_spreadsheet.worksheet.return_value = existing
    client.ensure_worksheet("Picks", ["A"])
    fake_spreadsheet.worksheet.reset_mock()

    client.append_row("Picks", ["v1"])
    client.append_row("Picks", ["v2"])

    # Cero lookups: viene del cache.
    fake_spreadsheet.worksheet.assert_not_called()
    assert existing.append_row.call_count == 2


def test_append_row_resolves_and_caches_if_not_ensured_first(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    existing = MagicMock(name="ws")
    fake_spreadsheet.worksheet.return_value = existing

    client.append_row("Picks", ["v1"])
    client.append_row("Picks", ["v2"])

    # Primer append resuelve, segundo viene del cache.
    assert fake_spreadsheet.worksheet.call_count == 1


def test_append_row_passes_user_entered_value_input_option(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    ws = MagicMock()
    fake_spreadsheet.worksheet.return_value = ws
    client.append_row("Picks", ["2026-05-23", "14:30", "EPL"])
    ws.append_row.assert_called_once_with(
        ["2026-05-23", "14:30", "EPL"],
        value_input_option=ValueInputOption.user_entered,
    )


# --- Propagación de errores (contrato outbox) -------------------------------


def test_ensure_worksheet_propagates_gspread_api_error(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    """Contrato: errores de gspread NO se silencian. El worker depende de esto
    para llamar `mark_failed` y dejar la fila para reintento."""
    fake_spreadsheet.worksheet.side_effect = gspread.exceptions.APIError(
        MagicMock(status_code=500, text="boom")
    )
    with pytest.raises(gspread.exceptions.APIError):
        client.ensure_worksheet("Picks", ["A"])


def test_append_row_propagates_gspread_api_error(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    ws = MagicMock()
    fake_spreadsheet.worksheet.return_value = ws
    ws.append_row.side_effect = gspread.exceptions.APIError(
        MagicMock(status_code=429, text="too many requests")
    )
    with pytest.raises(gspread.exceptions.APIError):
        client.append_row("Picks", ["x"])


def test_ensure_worksheet_propagates_add_worksheet_error(
    client: SheetsClient, fake_spreadsheet: MagicMock
) -> None:
    """Si la hoja no existe Y add_worksheet falla (sin permisos, drive scope
    faltante), debe propagar — no silenciar y dejar `_cache` sucio."""
    fake_spreadsheet.worksheet.side_effect = gspread.exceptions.WorksheetNotFound
    fake_spreadsheet.add_worksheet.side_effect = gspread.exceptions.APIError(
        MagicMock(status_code=403, text="forbidden")
    )
    with pytest.raises(gspread.exceptions.APIError):
        client.ensure_worksheet("Picks", ["A"])
    # Cache NO debe quedar con la hoja (no se creó realmente).
    assert "Picks" not in client._cache
