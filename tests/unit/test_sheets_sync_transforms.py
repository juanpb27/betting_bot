"""TDD de los mappers puros `delivery/sheets_sync.py`.

Mapean entidades de la DB → lista de strings/números (una fila de la hoja).
Sin gspread, sin red. Validamos:
- Que columnas coincidan con headers de DESIGN.md §9.
- Que campos post-settlement (P&L, CLV, ganados/perdidos) salgan como "" en E6.
- Que timestamps se rindan en TZ del proyecto (no UTC).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from betting_bot.delivery.sheets_sync import (
    BANKROLL_HEADERS,
    MOVEMENTS_HEADERS,
    PICKS_HEADERS,
    bankroll_snapshot_row,
    movement_to_row,
    pick_to_row,
)
from betting_bot.persistence.models import BankrollMovement
from tests.factories import build_pick


def test_picks_headers_match_design_doc() -> None:
    # Orden exacto según DESIGN.md §9.
    assert PICKS_HEADERS == [
        "Fecha",
        "Hora",
        "Liga",
        "Partido",
        "Mercado",
        "Outcome",
        "Cuota Min",
        "Cuota EU Ref",
        "EV %",
        "Stake Sugerido",
        "Estado",
        "Cuota Real",
        "Casa Real",
        "Stake Real",
        "Resultado",
        "P&L",
        "CLV",
        "Notas",
    ]


def test_movements_headers_match_design_doc() -> None:
    assert MOVEMENTS_HEADERS == [
        "Timestamp",
        "Casa",
        "Tipo",
        "Monto",
        "Pick ID",
        "Source",
        "Notas",
    ]


def test_bankroll_headers_match_design_doc() -> None:
    assert BANKROLL_HEADERS == [
        "Fecha",
        "Saldo BetPlay",
        "Saldo Codere",
        "Saldo Rushbet",
        "Saldo bwin",
        "Total",
        "Deposits Día",
        "Withdrawals Día",
        "Stakes Día",
        "Payouts Día",
        "Picks Hoy",
        "Ganados",
        "Perdidos",
        "P&L Día",
        "Drawdown vs Peak",
    ]


# --- pick_to_row ------------------------------------------------------------


def test_pick_to_row_pending_has_empty_post_settlement_columns() -> None:
    # Pick recién generado (status='pending'): cuota real, P&L, CLV vacíos.
    pick = build_pick(event_id="evt-1")
    pick.generated_at = datetime(2026, 5, 23, 19, 30, tzinfo=UTC)
    pick.generated_date = date(2026, 5, 23)
    pick.status = "pending"
    row = pick_to_row(pick, league_name="Premier League",
                      home_team="Arsenal", away_team="Chelsea")
    # Fecha y Hora en TZ proyecto (America/Bogota = UTC-5): 19:30 UTC → 14:30 COL.
    assert row[0] == "2026-05-23"
    assert row[1] == "14:30"
    assert row[2] == "Premier League"
    assert row[3] == "Arsenal vs Chelsea"
    assert row[4] == "h2h"
    assert row[5] == "home"
    # Estado.
    assert row[10] == "pending"
    # Campos post-settlement vacíos.
    assert row[11] == ""  # Cuota Real
    assert row[12] == ""  # Casa Real
    assert row[13] == ""  # Stake Real
    assert row[14] == ""  # Resultado
    assert row[15] == ""  # P&L
    assert row[16] == ""  # CLV


def test_pick_to_row_placed_includes_actual_fields() -> None:
    pick = build_pick(event_id="evt-1")
    pick.generated_at = datetime(2026, 5, 23, 19, 30, tzinfo=UTC)
    pick.generated_date = date(2026, 5, 23)
    pick.status = "placed"
    pick.actual_book = "betplay"
    pick.actual_price = 2.20
    pick.actual_stake = 27_500
    row = pick_to_row(pick, league_name="EPL", home_team="A", away_team="B")
    assert row[10] == "placed"
    assert row[11] == 2.20
    assert row[12] == "betplay"
    assert row[13] == 27_500


def test_pick_to_row_length_matches_headers() -> None:
    pick = build_pick(event_id="evt-1")
    pick.generated_at = datetime(2026, 5, 23, 19, 30, tzinfo=UTC)
    pick.generated_date = date(2026, 5, 23)
    row = pick_to_row(pick, league_name="x", home_team="A", away_team="B")
    assert len(row) == len(PICKS_HEADERS)


# --- movement_to_row --------------------------------------------------------


def test_movement_to_row_basic() -> None:
    mv = BankrollMovement(
        id=1,
        occurred_at=datetime(2026, 5, 23, 14, 0, tzinfo=UTC),
        book_code="betplay",
        movement_type="deposit",
        amount=500_000,
        related_pick_id=None,
        notes=None,
    )
    row = movement_to_row(mv, source="telegram")
    # Timestamp en TZ proyecto.
    assert row[0] == "2026-05-23 09:00"
    assert row[1] == "betplay"
    assert row[2] == "deposit"
    assert row[3] == 500_000
    assert row[4] == ""  # sin pick relacionado
    assert row[5] == "telegram"
    assert row[6] == ""  # sin notas


def test_movement_to_row_bet_stake_includes_pick_id_and_notes() -> None:
    mv = BankrollMovement(
        id=2,
        occurred_at=datetime(2026, 5, 23, 20, 0, tzinfo=UTC),
        book_code="codere",
        movement_type="bet_stake",
        amount=-25_000,
        related_pick_id="pick-uuid-abc",
        notes="wizard ya apostada",
    )
    row = movement_to_row(mv, source="wizard")
    assert row[3] == -25_000
    assert row[4] == "pick-uuid-abc"
    assert row[6] == "wizard ya apostada"


def test_movement_to_row_length_matches_headers() -> None:
    mv = BankrollMovement(
        id=1,
        occurred_at=datetime.now(UTC),
        book_code="betplay",
        movement_type="deposit",
        amount=1000,
        related_pick_id=None,
        notes=None,
    )
    assert len(movement_to_row(mv, source="x")) == len(MOVEMENTS_HEADERS)


# --- bankroll_snapshot_row --------------------------------------------------


def test_bankroll_snapshot_row_basic() -> None:
    row = bankroll_snapshot_row(
        snapshot_date=date(2026, 5, 23),
        balances={"betplay": 750_000, "codere": 600_000,
                  "rushbet": 625_000, "bwin": 500_000},
    )
    assert row[0] == "2026-05-23"
    assert row[1] == 750_000  # BetPlay
    assert row[2] == 600_000  # Codere
    assert row[3] == 625_000  # Rushbet
    assert row[4] == 500_000  # bwin
    assert row[5] == 2_475_000  # Total
    # Resto de columnas (post-settlement) vacías en E6.
    for i in range(6, 15):
        assert row[i] == "", f"col {i} debería estar vacía"


def test_bankroll_snapshot_row_handles_missing_books() -> None:
    # Si una casa no tiene saldo, mostrar 0 (no levantar).
    row = bankroll_snapshot_row(
        snapshot_date=date(2026, 5, 23),
        balances={"betplay": 100_000},
    )
    assert row[1] == 100_000
    assert row[2] == 0
    assert row[3] == 0
    assert row[4] == 0
    assert row[5] == 100_000


def test_bankroll_snapshot_row_length_matches_headers() -> None:
    row = bankroll_snapshot_row(snapshot_date=date(2026, 5, 23), balances={})
    assert len(row) == len(BANKROLL_HEADERS)


# --- TZ del proyecto --------------------------------------------------------


def test_pick_to_row_uses_project_timezone() -> None:
    # 04:00 UTC del 24 = 23:00 COL del 23 (UTC-5). Verificá que la fecha CAMBIA
    # según TZ, no se queda en UTC.
    pick = build_pick(event_id="evt-x")
    pick.generated_at = datetime(2026, 5, 24, 4, 0, tzinfo=UTC)
    pick.generated_date = date(2026, 5, 23)  # date ya calculado en TZ proyecto
    row = pick_to_row(pick, league_name="x", home_team="A", away_team="B")
    bogota = datetime(2026, 5, 24, 4, 0, tzinfo=UTC).astimezone(
        ZoneInfo("America/Bogota")
    )
    assert row[0] == bogota.strftime("%Y-%m-%d")
    assert row[1] == bogota.strftime("%H:%M")


def test_pick_to_row_handles_naive_datetime_as_utc() -> None:
    # SQLite con CURRENT_TIMESTAMP devuelve TIMESTAMP sin tz info. El mapper
    # asume UTC (es el contrato del schema). Sin este test, si alguien cambia
    # `_to_project` a raise o asumir localtime, los timestamps de Sheets se
    # desplazan silenciosamente.
    pick = build_pick(event_id="evt-naive")
    pick.generated_at = datetime(2026, 5, 23, 19, 30)  # naive
    pick.generated_date = date(2026, 5, 23)
    row = pick_to_row(pick, league_name="x", home_team="A", away_team="B")
    # 19:30 UTC asumido → 14:30 COL.
    assert row[0] == "2026-05-23"
    assert row[1] == "14:30"


def test_movement_to_row_handles_naive_datetime_as_utc() -> None:
    mv = BankrollMovement(
        id=99,
        occurred_at=datetime(2026, 5, 23, 14, 0),  # naive — viene de SQLite
        book_code="betplay",
        movement_type="deposit",
        amount=100_000,
        related_pick_id=None,
        notes=None,
    )
    row = movement_to_row(mv, source="telegram")
    # 14:00 UTC asumido → 09:00 COL.
    assert row[0] == "2026-05-23 09:00"


def test_pick_to_row_skipped_pick_has_empty_actual_columns() -> None:
    # Pick descartado: actual_* siguen None, no se debe romper el mapper.
    pick = build_pick(event_id="evt-skip")
    pick.generated_at = datetime(2026, 5, 23, 19, 30, tzinfo=UTC)
    pick.generated_date = date(2026, 5, 23)
    pick.status = "skipped"
    pick.skip_reason = "cuota local insuficiente"
    row = pick_to_row(pick, league_name="x", home_team="A", away_team="B")
    assert row[10] == "skipped"
    assert row[11] == ""  # Cuota Real
    assert row[12] == ""  # Casa Real
    assert row[13] == ""  # Stake Real


def test_bankroll_snapshot_row_handles_negative_balance() -> None:
    # Una casa puede quedar negativa transitoriamente (withdrawal antes que
    # llegue el payout). El mapper no debe filtrar ni explotar.
    row = bankroll_snapshot_row(
        snapshot_date=date(2026, 5, 23),
        balances={"betplay": -50_000, "codere": 100_000,
                  "rushbet": 0, "bwin": 0},
    )
    assert row[1] == -50_000
    assert row[2] == 100_000
    assert row[5] == 50_000  # total = -50k + 100k = 50k
