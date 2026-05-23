"""Worker que drena la cola `pending_sheets_sync` y escribe a Sheets.

`process_pending_row(row, session, sheets_client)` procesa UNA fila de la cola:
deserializa el payload, re-lee la entidad canónica de DB (no usa el payload
como fuente de verdad), mapea a row Sheets, y appendea. Devuelve `True` si
quedó completado o `False` si falló (la fila se marca con `mark_failed` y
volverá en próximos batches mientras `attempts < MAX_SHEETS_SYNC_ATTEMPTS`).

`drain_queue(session, sheets_client, limit)` toma `next_batch(limit)` y procesa
cada fila, devolviendo conteos. El caller (CLI) maneja lockfile + commit.

NO maneja transacciones — el caller es responsable del commit/rollback. El
worker hace `mark_completed`/`mark_failed` que mutan filas; al commitear el
scope ambas se persisten atómicas con el resto.
"""
from __future__ import annotations

import json
from datetime import date as date_t

from sqlalchemy.orm import Session

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery.sheets_client import SheetsClient
from betting_bot.delivery.sheets_sync import (
    BANKROLL_HEADERS,
    MOVEMENTS_HEADERS,
    PICKS_HEADERS,
    bankroll_snapshot_row,
    movement_to_row,
    pick_to_row,
)
from betting_bot.logging_setup import get_logger
from betting_bot.persistence.models import BankrollMovement, PendingSheetsSync
from betting_bot.persistence.repo import (
    PendingSheetsSyncRepo,
    PickRepo,
)

_log = get_logger(__name__)


def process_pending_row(
    row: PendingSheetsSync,
    *,
    session: Session,
    sheets_client: SheetsClient,
) -> bool:
    """Procesa una fila de la cola. Devuelve True si quedó completed, False
    si falló (y se llamó `mark_failed`). NO commitea — el caller lo hace."""
    queue = PendingSheetsSyncRepo(session)
    try:
        payload = json.loads(row.payload_json)
        if row.payload_type == "pick":
            _sync_pick(payload, session=session, sheets_client=sheets_client)
        elif row.payload_type == "movement":
            _sync_movement(payload, session=session, sheets_client=sheets_client)
        elif row.payload_type == "bankroll_snapshot":
            _sync_bankroll_snapshot(
                payload, session=session, sheets_client=sheets_client
            )
        else:
            raise ValueError(
                f"payload_type desconocido en runtime: {row.payload_type!r}"
            )
    except Exception as exc:
        _log.warning(
            "sheets_sync_failed",
            row_id=row.id,
            payload_type=row.payload_type,
            error=repr(exc),
            attempts=row.attempts + 1,
        )
        queue.mark_failed(row.id, error=repr(exc))
        return False
    queue.mark_completed(row.id)
    _log.info("sheets_sync_completed", row_id=row.id, payload_type=row.payload_type)
    return True


def drain_queue(
    *, session: Session, sheets_client: SheetsClient, limit: int = 50
) -> dict[str, int]:
    """Procesa hasta `limit` filas pendientes. Devuelve `{"completed", "failed"}`."""
    queue = PendingSheetsSyncRepo(session)
    counts = {"completed": 0, "failed": 0}
    for row in queue.next_batch(limit=limit):
        if process_pending_row(row, session=session, sheets_client=sheets_client):
            counts["completed"] += 1
        else:
            counts["failed"] += 1
    return counts


def _sync_pick(
    payload: dict[str, object], *, session: Session, sheets_client: SheetsClient
) -> None:
    pick_id = payload.get("pick_id")
    if not isinstance(pick_id, str):
        raise ValueError(f"payload pick sin pick_id válido: {payload!r}")
    result = PickRepo(session).get_with_event(pick_id)
    if result is None:
        raise ValueError(f"pick {pick_id!r} no existe en DB")
    pick, event = result
    sheets_client.ensure_worksheet("Picks", PICKS_HEADERS)
    sheets_client.append_row(
        "Picks",
        pick_to_row(
            pick,
            # Hoy: league_key crudo (`soccer_epl`). Etapa 7 / 8 va a sumar un
            # mapping `key → display name` ("Premier League") en leagues.yaml.
            league_name=event.league_key,
            home_team=event.home_team,
            away_team=event.away_team,
        ),
    )


def _sync_movement(
    payload: dict[str, object], *, session: Session, sheets_client: SheetsClient
) -> None:
    movement_id = payload.get("movement_id")
    if not isinstance(movement_id, int):
        raise ValueError(f"payload movement sin movement_id válido: {payload!r}")
    mv = session.get(BankrollMovement, movement_id)
    if mv is None:
        raise ValueError(f"movement {movement_id} no existe en DB")
    source = payload.get("source", "unknown")
    if not isinstance(source, str):
        source = "unknown"
    sheets_client.ensure_worksheet("Movements", MOVEMENTS_HEADERS)
    sheets_client.append_row("Movements", movement_to_row(mv, source=source))


def _sync_bankroll_snapshot(
    payload: dict[str, object], *, session: Session, sheets_client: SheetsClient
) -> None:
    snapshot_date_raw = payload.get("snapshot_date")
    if not isinstance(snapshot_date_raw, str):
        raise ValueError(
            f"payload bankroll_snapshot sin snapshot_date: {payload!r}"
        )
    snapshot_date = date_t.fromisoformat(snapshot_date_raw)
    balances = BankrollLedger(session).get_balance_by_book()
    sheets_client.ensure_worksheet("Bankroll", BANKROLL_HEADERS)
    sheets_client.append_row(
        "Bankroll",
        bankroll_snapshot_row(snapshot_date=snapshot_date, balances=balances),
    )
