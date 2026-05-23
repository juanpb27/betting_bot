"""Tests de los handlers puros de Telegram (TDD).

Los handlers son `handle_X(args, *, deps...) -> str`: funciones puras que
aplican side-effects (escrituras al ledger / system_state) y devuelven el
texto MarkdownV2 de respuesta. La capa async de python-telegram-bot
(`telegram_bot.py`) los envuelve; acá testeamos la lógica sin tocar Telegram.

Errores de input (book desconocido, monto inválido, overdraw) se modelan como
`ValueError` — el wrapper async los captura y los devuelve al usuario.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.delivery.telegram_handlers import (
    fmt_amount,
    handle_adjust,
    handle_balance,
    handle_bankroll,
    handle_deposit,
    handle_help,
    handle_pause,
    handle_resume,
    handle_start,
    handle_status,
    handle_withdraw,
    parse_amount,
    parse_book,
    parse_signed_amount,
)
from betting_bot.persistence.models import BankrollMovement
from betting_bot.persistence.repo import PickRepo, SystemStateRepo
from tests.factories import build_event, build_pick

# --- Parsers ----------------------------------------------------------------


def test_parse_amount_accepts_positive_integers() -> None:
    assert parse_amount("100000") == 100_000
    assert parse_amount("1") == 1


def test_parse_amount_rejects_zero_negative_decimal_garbage() -> None:
    for bad in ["0", "-100", "100.5", "abc", "", "1_000"]:
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_parse_signed_amount_accepts_signed_nonzero() -> None:
    assert parse_signed_amount("-5000") == -5000
    assert parse_signed_amount("+5000") == 5000
    assert parse_signed_amount("5000") == 5000


def test_parse_signed_amount_rejects_zero_and_garbage() -> None:
    for bad in ["0", "+0", "-0", "abc", "1.5"]:
        with pytest.raises(ValueError):
            parse_signed_amount(bad)


def test_parse_book_validates_against_books_yaml() -> None:
    # Casas reales de books.yaml destination_books.
    assert parse_book("betplay") == "betplay"
    assert parse_book("BetPlay") == "betplay"  # normaliza a lowercase
    with pytest.raises(ValueError, match="book_code desconocido"):
        parse_book("foo")


# --- Handlers de solo-lectura ----------------------------------------------


def test_handle_start_returns_welcome(session: Session) -> None:
    msg = handle_start()
    assert "betting" in msg.lower() or "bot" in msg.lower()


def test_handle_help_lists_all_commands() -> None:
    msg = handle_help()
    for cmd in ["/deposit", "/withdraw", "/adjust", "/balance", "/bankroll",
                "/status", "/pause", "/resume"]:
        assert cmd in msg


def test_handle_status_when_not_paused(session: Session) -> None:
    msg = handle_status(system_repo=SystemStateRepo(session))
    assert "activo" in msg.lower() or "running" in msg.lower() or "ok" in msg.lower()


def test_handle_status_when_paused(session: Session) -> None:
    repo = SystemStateRepo(session)
    repo.pause(reason="testing")
    msg = handle_status(system_repo=repo)
    assert "pausa" in msg.lower() or "paused" in msg.lower()
    assert "testing" in msg


def test_handle_balance_shows_per_book_and_total(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 500_000)
    ledger.record_deposit("codere", 300_000)
    msg = handle_balance(ledger=ledger)
    assert "betplay" in msg
    assert "codere" in msg
    assert "500" in msg  # algún formato del monto
    assert "800" in msg  # total


def test_handle_bankroll_includes_pending_picks_count(session: Session) -> None:
    # Bankroll vacío + 0 picks → cuenta = 0; agrega un pick pending.
    event = build_event()
    session.add(event)
    session.flush()
    pick = build_pick(
        event_id=event.id,
        generated_at=datetime(2026, 5, 22),
        generated_date=date(2026, 5, 22),
    )
    session.add(pick)
    session.flush()
    msg = handle_bankroll(ledger=BankrollLedger(session), pick_repo=PickRepo(session))
    assert "1" in msg  # 1 pending pick


# --- Handlers de escritura (deposit / withdraw / adjust) -------------------


def test_handle_deposit_persists_movement_and_returns_confirmation(
    session: Session,
) -> None:
    ledger = BankrollLedger(session)
    msg = handle_deposit(args=["betplay", "200000"], ledger=ledger)
    assert "betplay" in msg
    assert "200" in msg
    # Side-effect: movimiento persistido.
    total = session.execute(
        select(func.sum(BankrollMovement.amount)).where(
            BankrollMovement.book_code == "betplay"
        )
    ).scalar()
    assert total == 200_000


def test_handle_deposit_rejects_unknown_book(session: Session) -> None:
    with pytest.raises(ValueError, match="book_code desconocido"):
        handle_deposit(args=["foo", "100000"], ledger=BankrollLedger(session))


def test_handle_deposit_rejects_bad_amount(session: Session) -> None:
    with pytest.raises(ValueError):
        handle_deposit(args=["betplay", "-100"], ledger=BankrollLedger(session))


def test_handle_deposit_rejects_wrong_arg_count(session: Session) -> None:
    with pytest.raises(ValueError, match="uso"):
        handle_deposit(args=["betplay"], ledger=BankrollLedger(session))


def test_handle_withdraw_persists_negative_movement(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("codere", 500_000)
    msg = handle_withdraw(args=["codere", "100000"], ledger=ledger)
    assert "codere" in msg
    balance = ledger.get_balance_by_book()["codere"]
    assert balance == 400_000


def test_handle_withdraw_rejects_overdraw(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("rushbet", 50_000)
    with pytest.raises(ValueError, match="negativo"):
        handle_withdraw(args=["rushbet", "100000"], ledger=ledger)


def test_handle_adjust_with_signed_amount(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("bwin", 100_000)
    handle_adjust(args=["bwin", "-25000", "bonus", "expirado"], ledger=ledger)
    balance = ledger.get_balance_by_book()["bwin"]
    assert balance == 75_000


def test_handle_adjust_requires_reason(session: Session) -> None:
    with pytest.raises(ValueError, match="razón|reason|uso"):
        handle_adjust(args=["bwin", "-1000"], ledger=BankrollLedger(session))


# --- Handlers de control (pause / resume) ----------------------------------


def test_handle_pause_sets_system_state_with_reason(session: Session) -> None:
    repo = SystemStateRepo(session)
    handle_pause(args=["drawdown", "semanal"], system_repo=repo)
    state = repo.get()
    assert state.is_paused is True
    assert state.paused_reason is not None
    assert "drawdown" in state.paused_reason


def test_handle_pause_uses_default_reason_if_omitted(session: Session) -> None:
    repo = SystemStateRepo(session)
    handle_pause(args=[], system_repo=repo)
    state = repo.get()
    assert state.is_paused is True
    assert state.paused_reason is not None


def test_handle_resume_clears_pause(session: Session) -> None:
    repo = SystemStateRepo(session)
    repo.pause(reason="x")
    handle_resume(system_repo=repo)
    state = repo.get()
    assert state.is_paused is False
    assert state.paused_reason is None


# --- MarkdownV2 escape ------------------------------------------------------


# Caracteres MarkdownV2 que NUNCA forman entity y SIEMPRE deben escaparse
# fuera de un span `code`. Excluimos a propósito los que sí forman entities
# que usamos: `*` (bold), `_` (italic), `~` (strike), `[](url)` (link), `>`
# (blockquote). Esos requieren un parser real para distinguir uso correcto vs
# malformado; acá nos enfocamos en el bug que se nos coló en runtime: `.` y
# compañía dentro de montos.
_MD_V2_MUST_ESCAPE = ".#+-=|{}!"


def _assert_valid_markdown_v2(text: str) -> None:
    """Verifica que caracteres `_MD_V2_MUST_ESCAPE` fuera de spans `code`
    estén escapados con `\\`. Heurística — no es un parser MarkdownV2 completo
    — pero atrapa el error de runtime que vimos (separador de miles `.` que
    Telegram rechazaba con BadRequest).
    """
    in_code = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "`":
            in_code = not in_code
            i += 1
            continue
        if in_code:
            i += 1
            continue
        if ch == "\\":
            i += 2  # saltea el escape y el caracter escapado
            continue
        if ch in _MD_V2_MUST_ESCAPE:
            raise AssertionError(
                f"caracter reservado MarkdownV2 sin escapar en pos {i}: "
                f"{ch!r} en {text!r}"
            )
        i += 1


def testfmt_amount_wraps_value_in_backticks() -> None:
    # El `.` (separador de miles estilo COP) está RESERVADO en MarkdownV2.
    # Envolverlo en backticks evita escape carácter-por-carácter y a la vez
    # lo muestra en monospace.
    out = fmt_amount(1_000_000)
    assert out == "`1.000.000`"


def test_handle_balance_output_is_valid_markdown_v2(session: Session) -> None:
    # Regresión: bug detectado en runtime — `.` del formato de monto rompía
    # MarkdownV2. fmt_amount ahora envuelve en backticks.
    ledger = BankrollLedger(session)
    ledger.record_deposit("betplay", 1_500_000)
    ledger.record_deposit("codere", 750_000)
    _assert_valid_markdown_v2(handle_balance(ledger=ledger))


def test_handle_deposit_output_is_valid_markdown_v2(session: Session) -> None:
    msg = handle_deposit(args=["betplay", "250000"], ledger=BankrollLedger(session))
    _assert_valid_markdown_v2(msg)


def test_handle_adjust_output_is_valid_markdown_v2(session: Session) -> None:
    ledger = BankrollLedger(session)
    ledger.record_deposit("bwin", 200_000)
    msg = handle_adjust(
        args=["bwin", "-25000", "bonus", "expirado"], ledger=ledger
    )
    _assert_valid_markdown_v2(msg)


def test_handle_help_output_is_valid_markdown_v2() -> None:
    _assert_valid_markdown_v2(handle_help())


def test_handle_start_output_is_valid_markdown_v2() -> None:
    _assert_valid_markdown_v2(handle_start())


def test_pause_with_markdownv2_reserved_chars_renders_safely(
    session: Session,
) -> None:
    # Razón con todos los caracteres reservados de MarkdownV2: _ * [ ] ( ) ~
    # ` > # + - = | { } . !  Si /status los devuelve sin escape, Telegram
    # rechaza el parse_mode y el response llega como BadRequest.
    nasty = "drawdown _semanal_ (>5%) — fix #1.2 [a|b] = {x}!"
    repo = SystemStateRepo(session)
    handle_pause(args=[nasty], system_repo=repo)
    msg = handle_status(system_repo=repo)
    # Cada caracter reservado del input debe aparecer escapado con \\ en la salida.
    for ch in "_*[]()~`>#+-=|{}.!":
        if ch in nasty:
            # El caracter NO debe estar "desnudo" — debe haber al menos un
            # backslash directo previo en alguna ocurrencia.
            unescaped = msg.count(ch) - msg.count("\\" + ch)
            assert unescaped == 0, f"caracter {ch!r} aparece sin escapar en {msg!r}"
