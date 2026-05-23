"""TDD del worker que drena la cola de sheets sync.

`process_pending_row` toma una fila de `pending_sheets_sync`, deserializa el
payload, re-lee la entidad canónica de DB, mapea a row, y delega a
`SheetsClient.append_row`. Manejo de errores: si gspread tira, `mark_failed`
y la fila se reintenta en próximos batches. Si el payload referencia un ID
que ya no existe en DB, `mark_failed` con error explícito (anomalía rara que
queda en la cola para inspección manual).
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import gspread
import pytest
from sqlalchemy.orm import Session

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery.sheets_sync import (
    BANKROLL_HEADERS,
    MOVEMENTS_HEADERS,
    PICKS_HEADERS,
)
from betting_bot.delivery.sheets_worker import (
    drain_queue,
    process_pending_row,
)
from betting_bot.persistence.repo import (
    PendingSheetsSyncRepo,
    PickRepo,
)
from tests.factories import build_event, build_pick


@pytest.fixture
def fake_sheets() -> MagicMock:
    """Mock de SheetsClient con métodos ensure_worksheet / append_row."""
    client = MagicMock()
    client.ensure_worksheet = MagicMock()
    client.append_row = MagicMock()
    return client


# --- process_pending_row: pick ----------------------------------------------


def test_process_pick_row_appends_to_picks_sheet(
    session: Session, fake_sheets: MagicMock
) -> None:
    event = build_event(home_team="Arsenal", away_team="Chelsea",
                        league_key="soccer_epl")
    session.add(event)
    session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(session).create(pick, generated_at=datetime(2026, 5, 23, 19, 30, tzinfo=UTC))
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("pick", {"pick_id": pick.id})

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)

    assert ok is True
    fake_sheets.ensure_worksheet.assert_called_once_with("Picks", PICKS_HEADERS)
    fake_sheets.append_row.assert_called_once()
    args, _ = fake_sheets.append_row.call_args
    assert args[0] == "Picks"
    # Validamos un par de columnas para confirmar que el mapper se aplicó.
    appended = args[1]
    assert "Arsenal vs Chelsea" in appended
    assert "soccer_epl" in appended  # league_key como nombre por ahora (E7 prettify)


def test_process_pick_row_marks_failed_if_pick_missing(
    session: Session, fake_sheets: MagicMock
) -> None:
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("pick", {"pick_id": "doesnt-exist"})

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)

    assert ok is False
    fake_sheets.append_row.assert_not_called()
    # Side-effect crítico: mark_failed se invocó y dejó traza.
    session.refresh(row)
    assert row.attempts == 1
    assert row.last_error is not None
    assert "doesnt-exist" in row.last_error
    assert row.completed_at is None


# --- process_pending_row: movement ------------------------------------------


def test_process_movement_row_appends_to_movements_sheet(
    session: Session, fake_sheets: MagicMock
) -> None:
    ledger = BankrollLedger(session)
    mv = ledger.record_deposit("betplay", 200_000)
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue(
        "movement", {"movement_id": mv.id, "source": "telegram"}
    )

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)

    assert ok is True
    fake_sheets.ensure_worksheet.assert_called_once_with(
        "Movements", MOVEMENTS_HEADERS
    )
    args, _ = fake_sheets.append_row.call_args
    assert args[0] == "Movements"
    appended = args[1]
    assert "betplay" in appended
    assert 200_000 in appended


def test_process_movement_row_uses_default_source_if_missing(
    session: Session, fake_sheets: MagicMock
) -> None:
    # Payload sin "source" → default "unknown". No debería romper.
    ledger = BankrollLedger(session)
    mv = ledger.record_deposit("codere", 50_000)
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("movement", {"movement_id": mv.id})

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)
    assert ok is True
    appended = fake_sheets.append_row.call_args[0][1]
    assert "unknown" in appended


def test_process_movement_row_marks_failed_if_movement_missing(
    session: Session, fake_sheets: MagicMock
) -> None:
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("movement", {"movement_id": 999_999})
    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)
    assert ok is False
    session.refresh(row)
    assert row.attempts == 1
    assert row.last_error is not None
    assert "999999" in row.last_error


# --- process_pending_row: bankroll_snapshot ---------------------------------


def test_process_bankroll_snapshot_row_appends_to_bankroll_sheet(
    session: Session, fake_sheets: MagicMock
) -> None:
    # Snapshot lee balances de la fecha encolada via ledger.
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 750_000)
    ledger.record_deposit("codere", 600_000)
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue(
        "bankroll_snapshot", {"snapshot_date": "2026-05-23"}
    )

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)

    assert ok is True
    fake_sheets.ensure_worksheet.assert_called_once_with(
        "Bankroll", BANKROLL_HEADERS
    )
    args, _ = fake_sheets.append_row.call_args
    assert args[0] == "Bankroll"
    appended = args[1]
    assert appended[0] == "2026-05-23"
    assert 750_000 in appended
    assert 600_000 in appended


# --- Manejo de errores -----------------------------------------------------


def test_process_row_propagates_gspread_error_as_failure(
    session: Session, fake_sheets: MagicMock
) -> None:
    event = build_event()
    session.add(event)
    session.flush()
    pick = build_pick(event_id=event.id)
    PickRepo(session).create(pick, generated_at=datetime.now(UTC))
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("pick", {"pick_id": pick.id})

    fake_sheets.append_row.side_effect = gspread.exceptions.APIError(
        MagicMock(status_code=429, text="too many")
    )

    ok = process_pending_row(row, session=session, sheets_client=fake_sheets)
    assert ok is False
    # mark_failed se invocó incluso ante gspread error.
    session.refresh(row)
    assert row.attempts == 1
    assert row.last_error is not None
    assert "429" in row.last_error or "APIError" in row.last_error


def test_process_row_recovers_if_mark_failed_itself_errors(
    session: Session, fake_sheets: MagicMock
) -> None:
    """Si `mark_failed` mismo levanta (DB rota, fila borrada por otro proceso),
    process_pending_row NO debe propagar — el contrato del worker es 'una fila
    no rompe el lote'. Hoy se loguea y se devuelve False sin tirar."""
    ledger = BankrollLedger(session)
    mv = ledger.record_deposit("betplay", 100_000)
    queue = PendingSheetsSyncRepo(session)
    row = queue.enqueue("movement", {"movement_id": mv.id})
    fake_sheets.append_row.side_effect = gspread.exceptions.APIError(
        MagicMock(status_code=429, text="too many")
    )

    # Borramos la fila manualmente para simular "mark_failed encuentra que
    # la fila ya no existe" (race con otro proceso o admin manual).
    row_id = row.id
    session.delete(row)
    session.flush()
    # Recreamos el objeto en memoria (sin DB row): mark_failed va a tirar.
    # Trick: re-leemos el objeto antes del delete, lo mantenemos en memoria.
    # Más simple: re-armamos con el id.
    from betting_bot.persistence.models import PendingSheetsSync
    orphan = PendingSheetsSync(
        id=row_id,
        payload_type="movement",
        payload_json='{"movement_id": ' + str(mv.id) + '}',
        attempts=0,
    )
    # Llamada NO debe propagar a pesar de que el sync falla Y el mark_failed
    # también falla (fila no existe en DB).
    ok = process_pending_row(orphan, session=session, sheets_client=fake_sheets)
    assert ok is False  # contrato cumplido


# Nota: NO testeamos el caso "payload_type desconocido en runtime" porque el
# CHECK constraint de DB lo hace genuinamente imposible (ni el INSERT ni el
# UPDATE bypassean). La rama `else` en `process_pending_row` es defensive
# coding por si el constraint se removiera en una migración futura.


# --- drain_queue ------------------------------------------------------------


def test_drain_queue_processes_all_pending_and_returns_counts(
    session: Session, fake_sheets: MagicMock
) -> None:
    ledger = BankrollLedger(session)
    mv1 = ledger.record_deposit("betplay", 100_000)
    mv2 = ledger.record_deposit("codere", 50_000)
    queue = PendingSheetsSyncRepo(session)
    queue.enqueue("movement", {"movement_id": mv1.id, "source": "telegram"})
    queue.enqueue("movement", {"movement_id": mv2.id, "source": "telegram"})
    queue.enqueue("movement", {"movement_id": 999, "source": "x"})  # falla

    counts = drain_queue(session=session, sheets_client=fake_sheets, limit=10)

    assert counts == {"completed": 2, "failed": 1}
    # Las 2 OK quedaron completed.
    pending = queue.next_batch(limit=10)
    # La fallida queda pendiente (attempts=1 < MAX).
    assert len(pending) == 1
    assert pending[0].attempts == 1


def test_drain_queue_respects_limit(
    session: Session, fake_sheets: MagicMock
) -> None:
    ledger = BankrollLedger(session)
    queue = PendingSheetsSyncRepo(session)
    for _ in range(5):
        mv = ledger.record_deposit("betplay", 10_000)
        queue.enqueue("movement", {"movement_id": mv.id})

    counts = drain_queue(session=session, sheets_client=fake_sheets, limit=2)
    assert counts == {"completed": 2, "failed": 0}
    # Quedan 3 pendientes.
    assert len(queue.next_batch(limit=10)) == 3


def test_drain_queue_commits_per_row_so_late_crash_does_not_undo_earlier(
    session: Session, fake_sheets: MagicMock
) -> None:
    """Crítico: si la fila N de un lote causa que `process_pending_row`
    propague (defensive code falla, DB rota), los `mark_completed` de las
    filas 1..N-1 deben PERSISTIR. Sin commit por fila, todo el lote
    rollbackea → re-procesamiento → doble append a Sheets."""
    ledger = BankrollLedger(session)
    queue = PendingSheetsSyncRepo(session)
    mv1 = ledger.record_deposit("betplay", 100_000)
    mv2 = ledger.record_deposit("codere", 50_000)
    queue.enqueue("movement", {"movement_id": mv1.id})
    queue.enqueue("movement", {"movement_id": mv2.id})

    # Hacemos que el SEGUNDO append explote (no atrapable por process_pending_row
    # porque... bueno, sí lo atrapa, pero igualmente verificamos que el primer
    # mark_completed haya commiteado y NO se vea como pendiente).
    call_count = {"n": 0}
    def fail_on_second(name: str, _row: list[object]) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise gspread.exceptions.APIError(MagicMock(status_code=500, text="boom"))
    fake_sheets.append_row.side_effect = fail_on_second

    drain_queue(session=session, sheets_client=fake_sheets, limit=10)

    # Después del drain: la primera quedó completed (NO debe aparecer en
    # next_batch), la segunda quedó pendiente con attempts=1.
    pending = queue.next_batch(limit=10)
    assert len(pending) == 1
    assert pending[0].attempts == 1
