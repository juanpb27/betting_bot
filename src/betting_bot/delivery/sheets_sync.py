"""Mappers puros: entidades de DB → fila de Google Sheets.

Sin gspread, sin I/O. Las constantes `*_HEADERS` son el orden EXACTO de columnas
declarado en DESIGN.md §9 — si cambian, hay que actualizar el doc y la hoja
real (`SheetsClient.ensure_worksheet` recreará headers si la hoja no existe,
pero no migra una hoja existente).

Campos post-settlement (P&L, CLV, Resultado, ganados/perdidos del día) se
escriben como cadena vacía en Etapa 6 — los settearé Etapa 8 cuando el módulo
`settlement/` los calcule.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from betting_bot.config import get_settings
from betting_bot.persistence.models import BankrollMovement, Pick

PICKS_HEADERS: list[str] = [
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

MOVEMENTS_HEADERS: list[str] = [
    "Timestamp",
    "Casa",
    "Tipo",
    "Monto",
    "Pick ID",
    "Source",
    "Notas",
]

BANKROLL_HEADERS: list[str] = [
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

# Orden fijo de casas en la hoja Bankroll, según DESIGN §9 (no por allocation).
_BANKROLL_BOOK_ORDER: list[str] = ["betplay", "codere", "rushbet", "bwin"]


def _project_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _to_project(dt: datetime) -> datetime:
    """Asegura que `dt` esté en TZ del proyecto. Si es naive, asume UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_project_tz())


def pick_to_row(
    pick: Pick, *, league_name: str, home_team: str, away_team: str
) -> list[Any]:
    """Pick → fila de la hoja 'Picks'. `league_name`/`home_team`/`away_team`
    vienen del caller porque Pick no los embebe (vive como FK a Event)."""
    at = _to_project(pick.generated_at)
    return [
        at.strftime("%Y-%m-%d"),
        at.strftime("%H:%M"),
        league_name,
        f"{home_team} vs {away_team}",
        pick.market_key,
        pick.outcome,
        pick.min_odds_for_value,
        pick.comparison_price,
        round(pick.ev_at_comparison * 100, 2),
        pick.stake_recommended,
        pick.status,
        pick.actual_price if pick.actual_price is not None else "",
        pick.actual_book if pick.actual_book is not None else "",
        pick.actual_stake if pick.actual_stake is not None else "",
        "",  # Resultado (E8)
        "",  # P&L (E8)
        "",  # CLV (E8)
        pick.notes or "",
    ]


def movement_to_row(mv: BankrollMovement, *, source: str) -> list[Any]:
    """BankrollMovement → fila de la hoja 'Movements'. `source` indica origen
    ('telegram', 'wizard', 'settlement', 'reconciliation')."""
    at = _to_project(mv.occurred_at)
    return [
        at.strftime("%Y-%m-%d %H:%M"),
        mv.book_code,
        mv.movement_type,
        mv.amount,
        mv.related_pick_id or "",
        source,
        mv.notes or "",
    ]


def bankroll_snapshot_row(
    *, snapshot_date: date, balances: dict[str, int]
) -> list[Any]:
    """Snapshot diario → fila de la hoja 'Bankroll'. Las columnas
    post-settlement (deposits/withdrawals/stakes del día, ganados/perdidos,
    P&L, drawdown) quedan vacías hasta Etapa 8."""
    row: list[Any] = [snapshot_date.strftime("%Y-%m-%d")]
    total = 0
    for code in _BANKROLL_BOOK_ORDER:
        balance = balances.get(code, 0)
        row.append(balance)
        total += balance
    row.append(total)
    # 9 columnas post-settlement vacías.
    row.extend([""] * 9)
    return row
