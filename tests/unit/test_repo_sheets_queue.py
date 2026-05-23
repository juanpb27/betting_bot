"""TDD de `PendingSheetsSyncRepo` (Etapa 6).

La cola sirve para que el wiring del pipeline / wizard NO bloquee si Sheets
API está caída: enqueue es trivial (insert), el worker drena con retries.
Encolamos SOLO IDs (no payloads completos) — el worker re-lee del estado
canónico de DB.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from betting_bot.persistence.models import PendingSheetsSync
from betting_bot.persistence.repo import (
    MAX_SHEETS_SYNC_ATTEMPTS,
    PendingSheetsSyncRepo,
)


def test_enqueue_inserts_row_with_pending_state(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    row = repo.enqueue("pick", {"pick_id": "abc"})
    assert row.id is not None
    assert row.payload_type == "pick"
    assert "abc" in row.payload_json
    assert row.attempts == 0
    assert row.completed_at is None
    assert row.last_error is None


def test_enqueue_rejects_invalid_payload_type(session: Session) -> None:
    # Validación Python antes de tocar DB → mensaje útil, no IntegrityError
    # críptico de SQLite. El CHECK constraint queda como segunda red.
    repo = PendingSheetsSyncRepo(session)
    with pytest.raises(ValueError, match="payload_type inválido"):
        repo.enqueue("typo_unknown", {"x": 1})
    with pytest.raises(ValueError, match="payload_type inválido"):
        repo.enqueue("", {"x": 1})


def test_enqueue_accepts_each_valid_payload_type(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    for ptype in ("pick", "movement", "bankroll_snapshot"):
        row = repo.enqueue(ptype, {"id": ptype})
        assert row.payload_type == ptype


def test_next_batch_returns_only_pending_ordered_by_created_at(
    session: Session,
) -> None:
    repo = PendingSheetsSyncRepo(session)
    a = repo.enqueue("pick", {"pick_id": "1"})
    b = repo.enqueue("movement", {"movement_id": 2})
    c = repo.enqueue("bankroll_snapshot", {"snapshot_date": "2026-05-23"})
    repo.mark_completed(b.id)

    batch = repo.next_batch(limit=10)
    ids = [r.id for r in batch]
    assert ids == [a.id, c.id]


def test_next_batch_respects_limit(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    for i in range(5):
        repo.enqueue("pick", {"pick_id": f"p{i}"})
    assert len(repo.next_batch(limit=3)) == 3


def test_next_batch_excludes_dead_letter_rows(session: Session) -> None:
    """Filas con attempts >= MAX_SHEETS_SYNC_ATTEMPTS no se reintentan
    (dead-letter). El worker no las verá en su próximo batch."""
    repo = PendingSheetsSyncRepo(session)
    alive = repo.enqueue("pick", {"pick_id": "alive"})
    dying = repo.enqueue("pick", {"pick_id": "dying"})
    # Forzá a dying al borde del límite.
    for _ in range(MAX_SHEETS_SYNC_ATTEMPTS):
        repo.mark_failed(dying.id, error="429 too many")
    batch = repo.next_batch(limit=10)
    assert alive.id in [r.id for r in batch]
    assert dying.id not in [r.id for r in batch]


def test_next_batch_includes_rows_below_max_attempts(session: Session) -> None:
    # Una fila con N intentos pero N < MAX sigue elegible para reintento.
    repo = PendingSheetsSyncRepo(session)
    row = repo.enqueue("pick", {"pick_id": "transient"})
    repo.mark_failed(row.id, error="503 unavailable")
    repo.mark_failed(row.id, error="503 unavailable")
    assert row.id in [r.id for r in repo.next_batch(limit=10)]


def test_mark_completed_sets_completed_at(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    row = repo.enqueue("pick", {"pick_id": "k"})
    repo.mark_completed(row.id)
    fresh = session.get(PendingSheetsSync, row.id)
    assert fresh is not None
    assert fresh.completed_at is not None
    assert fresh.last_error is None  # éxito limpia errores previos


def test_mark_completed_on_nonexistent_row_raises(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    with pytest.raises(ValueError, match="no encontrado"):
        repo.mark_completed(999_999)


def test_mark_failed_increments_attempts_and_stores_error(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    row = repo.enqueue("pick", {"pick_id": "k"})
    repo.mark_failed(row.id, error="429 Too Many Requests")
    repo.mark_failed(row.id, error="503 Service Unavailable")
    fresh = session.get(PendingSheetsSync, row.id)
    assert fresh is not None
    assert fresh.attempts == 2
    assert fresh.last_error == "503 Service Unavailable"  # último gana
    assert fresh.last_attempt_at is not None
    assert fresh.completed_at is None


def test_mark_failed_on_nonexistent_row_raises(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    with pytest.raises(ValueError, match="no encontrado"):
        repo.mark_failed(999_999, error="x")


def test_count_dead_letter_counts_only_capped_rows(session: Session) -> None:
    repo = PendingSheetsSyncRepo(session)
    healthy = repo.enqueue("pick", {"pick_id": "h"})
    repo.mark_completed(healthy.id)
    transient = repo.enqueue("pick", {"pick_id": "t"})
    repo.mark_failed(transient.id, error="503")
    dead = repo.enqueue("pick", {"pick_id": "d"})
    for _ in range(MAX_SHEETS_SYNC_ATTEMPTS):
        repo.mark_failed(dead.id, error="403")

    assert repo.count_dead_letter() == 1
