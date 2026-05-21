from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from betting_bot.persistence.models import BankrollMovement
from betting_bot.yaml_config import load_book_codes


class BankrollLedger:
    """Operaciones sobre el ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _record(
        self,
        book_code: str,
        movement_type: str,
        amount: int,
        related_pick_id: str | None = None,
        notes: str | None = None,
    ) -> BankrollMovement:
        """Inserta un movimiento. `amount` ya viene con el signo correcto."""
        if book_code not in load_book_codes():
            raise ValueError(
                f"book_code desconocido: {book_code!r} (no está en config/books.yaml)"
            )
        movement = BankrollMovement(
            book_code=book_code,
            movement_type=movement_type,
            amount=amount,
            related_pick_id=related_pick_id,
            notes=notes,
        )
        self._session.add(movement)
        self._session.flush()  # asigna el id autoincrement sin commitear
        return movement

    def record_deposit(
        self, book_code: str, amount: int, notes: str | None = None
    ) -> BankrollMovement:
        """Registra un depósito a una casa. Entra plata."""
        if amount <= 0:
            raise ValueError("deposit: amount debe ser > 0")
        return self._record(book_code, "deposit", amount, notes=notes)

    def record_withdrawal(
        self, book_code: str, amount: int, notes: str | None = None
    ) -> BankrollMovement:
        """Registra un retiro de una casa. Sale plata."""
        if amount <= 0:
            raise ValueError("withdrawal: amount debe ser > 0")
        return self._record(book_code, "withdrawal", -amount, notes=notes)

    def record_bet_stake(
        self,
        book_code: str,
        amount: int,
        related_pick_id: str,
        notes: str | None = None,
    ) -> BankrollMovement:
        """Registra el stake apostado en un pick. Sale plata."""
        if amount <= 0:
            raise ValueError("bet_stake: amount debe ser > 0")
        return self._record(
            book_code, "bet_stake", -amount, related_pick_id=related_pick_id, notes=notes
        )

    def record_bet_payout(
        self,
        book_code: str,
        amount: int,
        related_pick_id: str,
        notes: str | None = None,
    ) -> BankrollMovement:
        """Registra el payout cobrado de un pick ganado. Entra plata."""
        if amount <= 0:
            raise ValueError("bet_payout: amount debe ser > 0")
        return self._record(
            book_code, "bet_payout", amount, related_pick_id=related_pick_id, notes=notes
        )

    def record_adjustment(
        self, book_code: str, signed_amount: int, notes: str | None = None
    ) -> BankrollMovement:
        """Registra un ajuste manual (reconciliación). El monto va con signo explícito."""
        if signed_amount == 0:
            raise ValueError("adjustment: signed_amount no puede ser 0")
        return self._record(book_code, "adjustment", signed_amount, notes=notes)

    def get_balance_by_book(self) -> dict[str, int]:
        """Saldo por casa. Incluye en 0 las casas conocidas sin movimientos."""
        balances: dict[str, int] = dict.fromkeys(load_book_codes(), 0)
        rows = self._session.execute(
            select(BankrollMovement.book_code, func.sum(BankrollMovement.amount)).group_by(
                BankrollMovement.book_code
            )
        ).all()
        for book_code, total in rows:
            balances[book_code] = int(total)
        return balances

    def get_total_balance(self) -> int:
        """Bankroll total vivo: suma de `amount` de todos los movimientos."""
        total = self._session.execute(
            select(func.sum(BankrollMovement.amount))
        ).scalar()
        return int(total) if total is not None else 0
