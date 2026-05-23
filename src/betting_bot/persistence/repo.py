"""Capa de acceso a datos sobre los modelos.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from betting_bot.config import get_settings
from betting_bot.persistence.models import (
    ApiQuotaLog,
    Event,
    OddsSnapshot,
    PendingSheetsSync,
    Pick,
    SystemState,
)


def _project_date(at: datetime) -> date:
    """Fecha calendario de `at` en la TZ del proyecto.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(ZoneInfo(get_settings().timezone)).date()


class EventRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: Event) -> Event:
        self._session.add(event)
        self._session.flush()
        return event

    def get(self, event_id: str) -> Event | None:
        return self._session.get(Event, event_id)

    def get_by_odds_api_id(self, odds_api_id: str) -> Event | None:
        return self._session.execute(
            select(Event).where(Event.odds_api_id == odds_api_id)
        ).scalar_one_or_none()

    def get_by_api_football_id(self, api_football_id: int) -> Event | None:
        return self._session.execute(
            select(Event).where(Event.api_football_id == api_football_id)
        ).scalar_one_or_none()

    def update(self, event: Event) -> Event:
        """Persiste cambios de un Event ya presente en la sesión."""
        self._session.flush()
        return event


class OddsRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, snapshot: OddsSnapshot) -> OddsSnapshot:
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def add_many(self, snapshots: list[OddsSnapshot]) -> None:
        self._session.add_all(snapshots)
        self._session.flush()

    def list_for_event(self, event_id: str) -> list[OddsSnapshot]:
        return list(
            self._session.execute(
                select(OddsSnapshot)
                .where(OddsSnapshot.event_id == event_id)
                .order_by(OddsSnapshot.captured_at.desc())
            ).scalars()
        )


class PickRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, pick: Pick, *, generated_at: datetime | None = None
    ) -> tuple[Pick, bool]:
        """Inserta un pick de forma idempotente.

        Devuelve `(pick, is_new)`:
        - `is_new=True` → era nuevo, se insertó (el pick recibido tiene id asignado).
        - `is_new=False` → ya existía un duplicado por (event_id, market_key,
          outcome, line, generated_date); el pick devuelto es el existente
          (objeto distinto al recibido), el caller no debe re-notificar.

        La tupla evita un contrato implícito ("el id está seteado solo si
        es nuevo"): callers como `run_pricing_and_notify` necesitan saber
        si debe encolar y notificar.
        """
        at = generated_at or datetime.now(UTC)
        pick.generated_at = at
        pick.generated_date = _project_date(at)

        existing = self._find_duplicate(pick)
        if existing is not None:
            return existing, False

        self._session.add(pick)
        self._session.flush()
        return pick, True

    def _find_duplicate(self, pick: Pick) -> Pick | None:
        line_filter = Pick.line.is_(None) if pick.line is None else Pick.line == pick.line
        return self._session.execute(
            select(Pick).where(
                Pick.event_id == pick.event_id,
                Pick.market_key == pick.market_key,
                Pick.outcome == pick.outcome,
                line_filter,
                Pick.generated_date == pick.generated_date,
            )
        ).scalar_one_or_none()

    def get(self, pick_id: str) -> Pick | None:
        return self._session.get(Pick, pick_id)

    def get_with_event(self, pick_id: str) -> tuple[Pick, Event] | None:
        """Devuelve `(pick, event)` en una sola query (JOIN). Útil para
        renderizar mensajes / filas Sheets que mezclan datos de ambos."""
        row = self._session.execute(
            select(Pick, Event)
            .join(Event, Event.id == Pick.event_id)
            .where(Pick.id == pick_id)
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    def list_by_status(self, status: str) -> list[Pick]:
        return list(
            self._session.execute(
                select(Pick).where(Pick.status == status).order_by(Pick.generated_at.desc())
            ).scalars()
        )

    def mark_placed(
        self,
        pick_id: str,
        *,
        actual_book: str,
        actual_price: float,
        actual_stake: int,
        placed_at: datetime | None = None,
    ) -> Pick:
        """Marca un pick como apostado. Rechaza si no está `pending` o si los
        datos son inválidos.

        IMPORTANTE: este método NO escribe a `bankroll_movements`. El caller
        (típicamente el wizard de Telegram) DEBE invocar
        `BankrollLedger.record_bet_stake(...)` en la misma sesión SQLAlchemy
        para que ambos cambios sean atómicos al commit del scope. Si
        `record_bet_stake` falla después, ambos se rollbackean.

        Cierra la race de doble-click vía UPDATE condicional sobre
        `status='pending'`: si dos handlers concurrentes leen ambos pending,
        solo el primer UPDATE matchea filas (rowcount==1); el segundo obtiene
        rowcount==0 y levanta `ValueError`.
        """
        if actual_price <= 1.0:
            raise ValueError(f"cuota inválida: {actual_price} (debe ser > 1.0)")
        if actual_stake <= 0:
            raise ValueError(f"stake inválido: {actual_stake} (debe ser > 0)")
        when = placed_at or datetime.now(UTC)
        # `Session.execute()` está tipado como `Result`, pero para DML
        # (UPDATE/DELETE) devuelve `CursorResult` con `.rowcount` — cast.
        result: CursorResult[Any] = self._session.execute(  # type: ignore[assignment]
            update(Pick)
            .where(Pick.id == pick_id, Pick.status == "pending")
            .values(
                status="placed",
                actual_book=actual_book,
                actual_price=actual_price,
                actual_stake=actual_stake,
                placed_at=when,
            )
        )
        if result.rowcount == 0:
            # O no existe o ya no está pending. Distinguimos con un get para
            # dar mensaje útil al wizard.
            pick = self._session.get(Pick, pick_id)
            if pick is None:
                raise ValueError(f"pick no encontrado: {pick_id!r}")
            raise ValueError(
                f"pick {pick_id!r} no está pending (status actual={pick.status!r})"
            )
        # Refresca el objeto de la sesión para que el caller vea los campos.
        self._session.flush()
        pick = self._session.get(Pick, pick_id)
        assert pick is not None  # acabamos de updatear, existe
        self._session.refresh(pick)
        return pick

    def mark_skipped(self, pick_id: str, reason: str) -> Pick:
        """Marca un pick como descartado. Rechaza si no está `pending` o si la
        razón está vacía.

        Mismo patrón de UPDATE condicional que `mark_placed` para cerrar el
        race de doble-click.
        """
        if not reason or not reason.strip():
            raise ValueError("reason no puede estar vacío")
        clean_reason = reason.strip()
        # `Session.execute()` está tipado como `Result`, pero para DML
        # (UPDATE/DELETE) devuelve `CursorResult` con `.rowcount` — cast.
        result: CursorResult[Any] = self._session.execute(  # type: ignore[assignment]
            update(Pick)
            .where(Pick.id == pick_id, Pick.status == "pending")
            .values(status="skipped", skip_reason=clean_reason)
        )
        if result.rowcount == 0:
            pick = self._session.get(Pick, pick_id)
            if pick is None:
                raise ValueError(f"pick no encontrado: {pick_id!r}")
            raise ValueError(
                f"pick {pick_id!r} no está pending (status actual={pick.status!r})"
            )
        self._session.flush()
        pick = self._session.get(Pick, pick_id)
        assert pick is not None
        self._session.refresh(pick)
        return pick


class SystemStateRepo:
    """Acceso al singleton `system_state` (id=1)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> SystemState:
        """Devuelve el estado del sistema, creándolo si aún no existe (id=1)."""
        state = self._session.get(SystemState, 1)
        if state is None:
            state = SystemState(id=1, is_paused=False)
            self._session.add(state)
            self._session.flush()
        return state

    def pause(self, reason: str, *, paused_at: datetime | None = None) -> SystemState:
        """Pone el sistema en pausa. Pausar dos veces sobrescribe la razón."""
        state = self.get()
        state.is_paused = True
        state.paused_reason = reason
        state.paused_at = paused_at or datetime.now(UTC)
        self._session.flush()
        return state

    def resume(self) -> SystemState:
        """Quita la pausa y limpia razón/timestamp. Idempotente sobre estado limpio."""
        state = self.get()
        state.is_paused = False
        state.paused_reason = None
        state.paused_at = None
        self._session.flush()
        return state


class QuotaRepo:
    """Acceso a `api_quota_log` — registro de consumo de cuota de las APIs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, log: ApiQuotaLog) -> ApiQuotaLog:
        self._session.add(log)
        self._session.flush()
        return log


_VALID_PAYLOAD_TYPES: frozenset[str] = frozenset(
    {"pick", "movement", "bankroll_snapshot"}
)
# Filas con N intentos fallidos pasan a "dead-letter": no se reintentan más.
# Hay que mirarlas a mano (CLI futuro o query directa). Conservador: 10 intentos
# cubre rate limits transitorios (429 con backoff exponencial) y descarta los
# errores permanentes (403, payload malformado).
MAX_SHEETS_SYNC_ATTEMPTS = 10


class PendingSheetsSyncRepo:
    """Cola de IDs pendientes de sincronizar a Google Sheets.

    Patrón outbox simplificado: el pipeline / wizard encolan SOLO el ID de la
    entidad (pick_id, movement_id) y el `payload_type`. El worker re-lee la
    entidad de DB al procesar la fila — así Sheets siempre refleja el estado
    canónico de la DB, no un snapshot stale que pudo haber quedado encolado.

    Para snapshots de bankroll (que no tienen entidad propia en DB porque se
    computan al vuelo), el payload incluye `{"snapshot_date": "..."}` y el
    worker pide el snapshot al ledger.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self, payload_type: str, payload: dict[str, Any]
    ) -> PendingSheetsSync:
        """Encola un trabajo de sync. Valida `payload_type` antes de tocar DB
        (mensaje útil en vez de IntegrityError críptico de SQLite)."""
        if payload_type not in _VALID_PAYLOAD_TYPES:
            raise ValueError(
                f"payload_type inválido: {payload_type!r}. "
                f"Esperado uno de: {sorted(_VALID_PAYLOAD_TYPES)}"
            )
        row = PendingSheetsSync(
            payload_type=payload_type,
            # Payload chico (IDs + fechas). default=str cubre date/datetime;
            # cualquier tipo no-serializable se convierte a su repr y queda
            # registrado — el worker debe leer del campo canónico (DB), no del
            # payload, así que aunque el repr sea feo no rompe el flujo.
            payload_json=json.dumps(payload, default=str),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def next_batch(self, limit: int = 50) -> list[PendingSheetsSync]:
        """Devuelve hasta `limit` filas pendientes (completed_at IS NULL,
        attempts < MAX), ordenadas por created_at ASC (FIFO). Filas con
        attempts >= MAX_SHEETS_SYNC_ATTEMPTS son dead-letter — no se reintentan."""
        return list(
            self._session.execute(
                select(PendingSheetsSync)
                .where(
                    PendingSheetsSync.completed_at.is_(None),
                    PendingSheetsSync.attempts < MAX_SHEETS_SYNC_ATTEMPTS,
                )
                .order_by(PendingSheetsSync.created_at.asc())
                .limit(limit)
            ).scalars()
        )

    def mark_completed(self, row_id: int) -> PendingSheetsSync:
        """Marca la fila como sincronizada con éxito y limpia el último error."""
        row = self._session.get(PendingSheetsSync, row_id)
        if row is None:
            raise ValueError(f"pending_sheets_sync no encontrado: {row_id}")
        row.completed_at = datetime.now(UTC)
        row.last_error = None
        self._session.flush()
        return row

    def mark_failed(self, row_id: int, error: str) -> PendingSheetsSync:
        """Suma 1 al contador de intentos y guarda el último error. La fila
        sigue pendiente hasta que attempts alcance MAX_SHEETS_SYNC_ATTEMPTS."""
        row = self._session.get(PendingSheetsSync, row_id)
        if row is None:
            raise ValueError(f"pending_sheets_sync no encontrado: {row_id}")
        row.attempts += 1
        row.last_error = error
        row.last_attempt_at = datetime.now(UTC)
        self._session.flush()
        return row

    def count_dead_letter(self) -> int:
        """Cuenta filas que alcanzaron MAX_SHEETS_SYNC_ATTEMPTS sin éxito.
        Útil para `/sheets_status` (Etapa 8) o smoke checks de operador."""
        result = self._session.execute(
            select(func.count())
            .select_from(PendingSheetsSync)
            .where(
                PendingSheetsSync.completed_at.is_(None),
                PendingSheetsSync.attempts >= MAX_SHEETS_SYNC_ATTEMPTS,
            )
        ).scalar()
        return int(result or 0)
