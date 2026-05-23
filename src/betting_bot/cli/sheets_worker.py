"""CLI: drena la cola `pending_sheets_sync` escribiendo a Google Sheets.

Uso:
    uv run python -m betting_bot.cli.sheets_worker           # drena todo
    uv run python -m betting_bot.cli.sheets_worker --once    # un solo lote
    uv run python -m betting_bot.cli.sheets_worker --limit 100

Lockfile en `data/sheets_worker.lock` evita doble-process si el operador
dispara dos veces el comando o si systemd se solapa con una corrida manual.
Si otro proceso tiene el lock, el comando exit con código 0 y log de skip.
"""
from __future__ import annotations

import fcntl
import sys

import click
from pydantic import ValidationError
from rich.console import Console
from sqlalchemy import create_engine

from betting_bot.config import get_settings
from betting_bot.delivery.sheets_client import SheetsClient
from betting_bot.delivery.sheets_worker import drain_queue
from betting_bot.logging_setup import bind_request_id, configure_logging, get_logger
from betting_bot.persistence.db import apply_sqlite_pragmas, resolve_database_url, session_scope

console = Console()


@click.command()
@click.option("--once", is_flag=True, help="Procesa un solo lote y sale.")
@click.option("--limit", default=50, show_default=True, help="Filas por lote.")
def main(once: bool, limit: int) -> None:
    """Drena `pending_sheets_sync` escribiendo a Google Sheets."""
    try:
        settings = get_settings()
    except ValidationError as e:
        console.print("[bold red]Error de configuración (.env):[/bold red]")
        for err in e.errors():
            field_name = ".".join(str(x) for x in err["loc"])
            console.print(f"  • [yellow]{field_name}[/yellow]: {err['msg']}")
        sys.exit(1)

    configure_logging(level=settings.log_level)
    log = get_logger(__name__)

    lock_path = settings.data_dir / "sheets_worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # noqa: el lockfile debe vivir lo que dure el proceso; cerrarlo con `with`
    # liberaría el lock al salir del bloque, defeating el propósito.
    lock_fp = open(lock_path, "w")  # noqa: SIM115
    try:
        # Non-blocking: si otro proceso tiene el lock, fallamos limpio.
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("sheets_worker_skipped_lock_held", lock=str(lock_path))
        console.print(
            "[yellow]Otro sheets_worker está corriendo (lockfile tomado). "
            "Skip.[/yellow]"
        )
        sys.exit(0)

    # Engine compartido con el resto del proyecto (mismo DB que el listener +
    # pipeline). resolve_database_url evita el bug de cwd.
    engine = create_engine(resolve_database_url())
    apply_sqlite_pragmas(engine)
    sheets = SheetsClient(
        spreadsheet_id=settings.google_sheet_id,
        sa_path=str(settings.google_service_account_json_path),
    )

    total = {"completed": 0, "failed": 0}
    with bind_request_id() as rid:
        log.info("sheets_worker_started", once=once, limit=limit)
        while True:
            with session_scope() as session:
                counts = drain_queue(
                    session=session, sheets_client=sheets, limit=limit
                )
            total["completed"] += counts["completed"]
            total["failed"] += counts["failed"]
            console.print(
                f"  lote: {counts['completed']} completed, "
                f"{counts['failed']} failed"
            )
            # Salir si: --once, o si no quedó nada por procesar.
            processed = counts["completed"] + counts["failed"]
            if once or processed == 0:
                break
        log.info(
            "sheets_worker_done",
            completed=total["completed"],
            failed=total["failed"],
        )
        _ = rid  # silencia "unused" — vive en contextvars

    console.print(
        f"[bold green]Done.[/bold green] "
        f"Completed: {total['completed']} | Failed: {total['failed']}"
    )


if __name__ == "__main__":
    main()
