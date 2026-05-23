"""Handlers puros de los comandos de Telegram.

Cada `handle_X` es una función pura I/O (con side-effects sobre la DB pero sin
tocar la API de Telegram) que devuelve el texto MarkdownV2 a enviar como
respuesta. La capa async (`telegram_bot.py`) la envuelve, inyecta una sesión
de SQLAlchemy y captura `ValueError` para devolverlos al usuario.

Errores de input (book desconocido, monto inválido, overdraw, argc) se
modelan como `ValueError`. Cualquier otra excepción es un bug.
"""
from __future__ import annotations

from betting_bot.bankroll.ledger import BankrollLedger
from betting_bot.persistence.repo import PickRepo, SystemStateRepo
from betting_bot.yaml_config import load_book_codes

# MarkdownV2 reserva estos caracteres fuera de los rangos de formato.
_MD_V2_RESERVED = r"_*[]()~`>#+-=|{}.!\\"


def escape_md(text: str) -> str:
    """Escapa caracteres reservados de MarkdownV2 para texto plano."""
    out = []
    for ch in text:
        if ch in _MD_V2_RESERVED:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


# --- Parsers ----------------------------------------------------------------


def parse_amount(s: str) -> int:
    """Entero positivo, sin signo ni decimales. Rechaza '0', '-X', 'X.Y', etc."""
    if not s or not s.isdigit() or s == "0":
        raise ValueError(f"monto inválido: {s!r} (esperado entero positivo)")
    return int(s)


def parse_signed_amount(s: str) -> int:
    """Entero no-cero con signo opcional. '5000', '+5000', '-5000' OK; '0' no."""
    if not s:
        raise ValueError("monto inválido: vacío")
    body = s[1:] if s[0] in "+-" else s
    if not body.isdigit():
        raise ValueError(f"monto inválido: {s!r} (esperado entero con signo)")
    value = int(s)
    if value == 0:
        raise ValueError("monto inválido: 0 no es ajuste válido")
    return value


def parse_book(s: str) -> str:
    """Normaliza a lowercase y valida contra los códigos de `books.yaml`."""
    code = s.strip().lower()
    if code not in load_book_codes():
        raise ValueError(
            f"book_code desconocido: {s!r} (no está en config/books.yaml)"
        )
    return code


# --- Helpers de formato -----------------------------------------------------


def fmt_amount(n: int) -> str:
    """Formato de monto con separador de miles, envuelto en backticks.

    Devuelve `` `1.000.000` ``: monospace y, lo más importante, los puntos no
    rompen el parse de MarkdownV2 (dentro de `code` los caracteres reservados
    no necesitan escape). Soporta enteros negativos.
    """
    formatted = f"{n:,}".replace(",", ".")  # 1.000.000 estilo COP
    return f"`{formatted}`"


# --- Handlers ---------------------------------------------------------------


def handle_start() -> str:
    return (
        "*Betting Bot* — value betting en fútbol\\.\n\n"
        "Comandos: /help\n"
        "Estado: /status"
    )


def handle_help() -> str:
    # Cada `.` se escapa porque MarkdownV2 lo reserva.
    return (
        "*Comandos disponibles*\n\n"
        "*Consulta*\n"
        "/status — estado del sistema \\(pausado o activo\\)\n"
        "/balance — saldo por casa y total\n"
        "/bankroll — saldo \\+ picks pendientes\n\n"
        "*Bankroll*\n"
        "/deposit `<book>` `<amount>` — registrar depósito\n"
        "/withdraw `<book>` `<amount>` — registrar retiro\n"
        "/adjust `<book>` `<signed_amount>` `<reason>` — ajuste manual\n\n"
        "*Control*\n"
        "/pause `[reason]` — pausar generación de picks\n"
        "/resume — reanudar"
    )


def handle_status(*, system_repo: SystemStateRepo) -> str:
    state = system_repo.get()
    if state.is_paused:
        reason = escape_md(state.paused_reason or "sin razón registrada")
        return f"*PAUSADO*\nRazón: {reason}"
    return "*ACTIVO* — sistema operando normalmente"


def handle_balance(*, ledger: BankrollLedger) -> str:
    balances = ledger.get_balance_by_book()
    total = ledger.get_total_balance()
    lines = ["*Balance por casa*"]
    for book, amount in sorted(balances.items()):
        lines.append(f"`{book}`: {fmt_amount(amount)}")
    lines.append("")
    lines.append(f"*Total*: {fmt_amount(total)}")
    return "\n".join(lines)


def handle_bankroll(*, ledger: BankrollLedger, pick_repo: PickRepo) -> str:
    total = ledger.get_total_balance()
    pending = len(pick_repo.list_by_status("pending"))
    return (
        f"*Bankroll vivo*: {fmt_amount(total)}\n"
        f"*Picks pendientes*: {pending}"
    )


def handle_deposit(*, args: list[str], ledger: BankrollLedger) -> str:
    if len(args) != 2:
        raise ValueError("uso: /deposit <book> <amount>")
    book = parse_book(args[0])
    amount = parse_amount(args[1])
    ledger.record_deposit(book, amount)
    new_balance = ledger.get_balance_by_book()[book]
    return (
        f"Depósito registrado: \\+{fmt_amount(amount)} en `{book}`\n"
        f"Saldo `{book}`: {fmt_amount(new_balance)}"
    )


def handle_withdraw(*, args: list[str], ledger: BankrollLedger) -> str:
    if len(args) != 2:
        raise ValueError("uso: /withdraw <book> <amount>")
    book = parse_book(args[0])
    amount = parse_amount(args[1])
    ledger.record_withdrawal(book, amount)
    new_balance = ledger.get_balance_by_book()[book]
    return (
        f"Retiro registrado: \\-{fmt_amount(amount)} de `{book}`\n"
        f"Saldo `{book}`: {fmt_amount(new_balance)}"
    )


def handle_adjust(*, args: list[str], ledger: BankrollLedger) -> str:
    if len(args) < 3:
        raise ValueError("uso: /adjust <book> <signed_amount> <razón>")
    book = parse_book(args[0])
    signed = parse_signed_amount(args[1])
    reason = " ".join(args[2:])
    ledger.record_adjustment(book, signed, notes=reason)
    new_balance = ledger.get_balance_by_book()[book]
    sign = "\\+" if signed > 0 else "\\-"
    return (
        f"Ajuste registrado: {sign}{fmt_amount(abs(signed))} en `{book}` "
        f"\\({escape_md(reason)}\\)\n"
        f"Saldo `{book}`: {fmt_amount(new_balance)}"
    )


def handle_pause(*, args: list[str], system_repo: SystemStateRepo) -> str:
    reason = " ".join(args).strip() or "manual pause via Telegram"
    system_repo.pause(reason=reason)
    return f"*Sistema pausado*\nRazón: {escape_md(reason)}"


def handle_resume(*, system_repo: SystemStateRepo) -> str:
    system_repo.resume()
    return "*Sistema reanudado*"
