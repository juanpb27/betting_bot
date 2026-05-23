"""Proceso long-running del bot de Telegram.

Uso:
    uv run python -m betting_bot.cli.telegram_listener

En prod lo dispara `betting-bot-telegram.service` (systemd, Etapa 10). En dev
se corre a mano: el `Application.run_polling()` es bloqueante; Ctrl+C para
salir.
"""
from __future__ import annotations

import sys

from pydantic import ValidationError
from rich.console import Console
from sqlalchemy import create_engine

from betting_bot.config import get_settings
from betting_bot.delivery.telegram_bot import build_application
from betting_bot.logging_setup import configure_logging, get_logger
from betting_bot.persistence.db import apply_sqlite_pragmas, resolve_database_url

console = Console()


def main() -> None:
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

    engine = create_engine(resolve_database_url())
    apply_sqlite_pragmas(engine)

    app = build_application(
        token=settings.telegram_bot_token,
        authorized_chat_id=settings.telegram_chat_id,
        engine=engine,
    )

    console.print(
        f"[bold green]Bot iniciado.[/bold green] Autorizado chat_id="
        f"[cyan]{settings.telegram_chat_id}[/cyan]. Ctrl+C para salir."
    )
    log.info("telegram_listener_started", authorized_chat_id=settings.telegram_chat_id)
    # Bloqueante. Polling = no necesita IP pública ni webhook.
    # drop_pending_updates: descarta mensajes acumulados mientras el bot estuvo
    # offline. Sin esto, al arrancar procesaría el backlog completo (típicamente
    # ruido de pruebas viejas o mensajes a destiempo).
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
