"""
Verifica conectividad con todos los servicios externos requeridos.

Uso:
    uv run python -m betting_bot.cli.healthcheck

Salida: tabla Rich con estado de cada servicio. Exit code 0 si todo OK, 1 si algo falla.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

import gspread
import httpx
from google.oauth2.service_account import Credentials
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from betting_bot.config import get_settings

console = Console()


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    extra: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Checks individuales
# ---------------------------------------------------------------------------


async def check_odds_api(client: httpx.AsyncClient) -> CheckResult:
    """Ping a the-odds-api y devuelve los deportes disponibles como sanity check."""
    settings = get_settings()
    try:
        r = await client.get(
            "https://api.the-odds-api.com/v4/sports/",
            params={"apiKey": settings.odds_api_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        sports_count = len(data)
        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        return CheckResult(
            name="the-odds-api",
            ok=True,
            detail=f"{sports_count} deportes disponibles",
            extra={"quota_remaining": remaining, "quota_used": used},
        )
    except httpx.HTTPStatusError as e:
        return CheckResult(name="the-odds-api", ok=False, detail=f"HTTP {e.response.status_code}: {e.response.text[:120]}")
    except Exception as e:
        return CheckResult(name="the-odds-api", ok=False, detail=str(e))


async def check_api_football(client: httpx.AsyncClient) -> CheckResult:
    """Verifica cuenta y cuota en api-sports.io."""
    settings = get_settings()
    try:
        r = await client.get(
            "https://v3.football.api-sports.io/status",
            headers={"x-apisports-key": settings.api_football_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        # api-football devuelve HTTP 200 con `errors` poblado cuando la key es
        # inválida o el plan expiró. raise_for_status() NO lo detecta: hay que
        # inspeccionar el cuerpo de la respuesta explícitamente.
        errors = data.get("errors")
        if errors:
            return CheckResult(
                name="api-football", ok=False, detail=f"API respondió con errores: {errors}"
            )

        response = data.get("response", {})
        account = response.get("account", {})
        subscription = response.get("subscription", {})
        requests_info = response.get("requests", {})

        # Si no hay email de cuenta, la respuesta no es la esperada → fallar, no asumir OK.
        if not account.get("email"):
            return CheckResult(
                name="api-football", ok=False, detail=f"respuesta inesperada (sin cuenta): {data}"
            )

        plan = subscription.get("plan", "?")
        limit_day = requests_info.get("limit_day")
        current = requests_info.get("current", 0)
        remaining = limit_day - current if isinstance(limit_day, int) else "?"
        return CheckResult(
            name="api-football",
            ok=True,
            detail=f"Cuenta: {account.get('email')} — Plan: {plan}",
            extra={"quota_remaining_today": str(remaining)},
        )
    except httpx.HTTPStatusError as e:
        return CheckResult(name="api-football", ok=False, detail=f"HTTP {e.response.status_code}: {e.response.text[:120]}")
    except Exception as e:
        return CheckResult(name="api-football", ok=False, detail=str(e))


async def check_telegram(client: httpx.AsyncClient) -> CheckResult:
    """Verifica token de bot y acceso al chat."""
    settings = get_settings()
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        # getMe: verifica token. Telegram puede devolver {"ok": false, ...} con
        # HTTP 200, así que chequeamos el campo `ok` explícitamente.
        r = await client.get(f"{base}/getMe", timeout=10)
        r.raise_for_status()
        payload = r.json()
        if not payload.get("ok"):
            return CheckResult(
                name="Telegram", ok=False, detail=f"getMe ok=false: {payload.get('description', '?')}"
            )
        bot_data = payload.get("result", {})
        bot_username = bot_data.get("username", "?")

        # getChat: verifica que el chat_id sea accesible
        r2 = await client.get(f"{base}/getChat", params={"chat_id": settings.telegram_chat_id}, timeout=10)
        r2.raise_for_status()
        payload2 = r2.json()
        if not payload2.get("ok"):
            return CheckResult(
                name="Telegram", ok=False, detail=f"getChat ok=false: {payload2.get('description', '?')}"
            )
        chat_data = payload2.get("result", {})
        chat_type = chat_data.get("type", "?")

        return CheckResult(
            name="Telegram",
            ok=True,
            detail=f"Bot: @{bot_username} — Chat: {settings.telegram_chat_id} ({chat_type})",
        )
    except httpx.HTTPStatusError as e:
        return CheckResult(name="Telegram", ok=False, detail=f"HTTP {e.response.status_code}: {e.response.text[:120]}")
    except Exception as e:
        return CheckResult(name="Telegram", ok=False, detail=str(e))


async def check_google_sheets() -> CheckResult:
    """Verifica acceso al Google Sheet con las credenciales de service account."""
    settings = get_settings()
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        sa_path = settings.google_service_account_json_path
        if not sa_path.exists():
            return CheckResult(
                name="Google Sheets",
                ok=False,
                detail=f"Archivo service account no encontrado: {sa_path}",
            )

        # google-auth no anota from_service_account_file → ignore puntual del strict mode.
        creds = Credentials.from_service_account_file(str(sa_path), scopes=scopes)  # type: ignore[no-untyped-call]
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(settings.google_sheet_id)
        worksheets = sh.worksheets()
        sheet_names = [ws.title for ws in worksheets]

        return CheckResult(
            name="Google Sheets",
            ok=True,
            detail=f"'{sh.title}' — {len(sheet_names)} hojas: {', '.join(sheet_names)}",
        )
    except Exception as e:
        return CheckResult(name="Google Sheets", ok=False, detail=str(e)[:200])


async def check_healthchecks(client: httpx.AsyncClient) -> CheckResult:
    """Valida el formato de HEALTHCHECKS_URL y la reachability del host hc-ping.com.
    """
    settings = get_settings()
    url = settings.healthchecks_url
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "hc-ping.com" or len(parsed.path) <= 1:
        return CheckResult(
            name="healthchecks.io",
            ok=False,
            detail=f"HEALTHCHECKS_URL con formato inesperado: {url!r} "
            f"(esperado https://hc-ping.com/<uuid>)",
        )
    try:
        # Probe al host root, NO al check del usuario. follow_redirects=False:
        # un 301 del propio host ya prueba que es alcanzable (DNS + TLS + servicio).
        r = await client.get("https://hc-ping.com/", follow_redirects=False, timeout=10)
        return CheckResult(
            name="healthchecks.io",
            ok=True,
            detail=f"URL válida, host alcanzable (HTTP {r.status_code}) — sin ping al check",
        )
    except Exception as e:
        return CheckResult(name="healthchecks.io", ok=False, detail=f"host inalcanzable: {e}")


# ---------------------------------------------------------------------------
# Captura de fixtures para tests
# ---------------------------------------------------------------------------


async def capture_fixtures(client: httpx.AsyncClient) -> None:
    """Guarda respuestas reales sanitizadas a tests/fixtures/."""
    settings = get_settings()
    fixtures_dir = get_settings().data_dir.parent / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    console.print("\n[bold]Capturando fixtures para tests...[/bold]")

    # the-odds-api: sports list
    try:
        r = await client.get(
            "https://api.the-odds-api.com/v4/sports/",
            params={"apiKey": settings.odds_api_key},
            timeout=10,
        )
        path = fixtures_dir / "odds_api_sports.json"
        path.write_text(json.dumps(r.json(), indent=2, ensure_ascii=False))
        console.print(f"  ✅ {path.name}")
    except Exception as e:
        console.print(f"  ❌ odds_api_sports.json: {e}")

    # the-odds-api: odds EPL h2h Pinnacle
    try:
        r = await client.get(
            "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/",
            params={
                "apiKey": settings.odds_api_key,
                "regions": "eu",
                "markets": "h2h",
                "bookmakers": "pinnacle,bet365,betsson",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=15,
        )
        data = r.json()
        sample = data[:3] if isinstance(data, list) else data
        path = fixtures_dir / "odds_api_epl_h2h_sample.json"
        path.write_text(json.dumps(sample, indent=2, ensure_ascii=False))
        console.print(f"  ✅ {path.name} ({len(sample) if isinstance(sample, list) else '?'} eventos)")
    except Exception as e:
        console.print(f"  ❌ odds_api_epl_h2h_sample.json: {e}")

    # api-football: status (sin datos sensibles)
    try:
        r = await client.get(
            "https://v3.football.api-sports.io/status",
            headers={"x-apisports-key": settings.api_football_key},
            timeout=10,
        )
        data = r.json()
        # Sanitizar datos personales antes de guardar (email + nombre).
        account = data.get("response", {}).get("account", {})
        if "email" in account:
            account["email"] = "REDACTED@example.com"
        if "firstname" in account:
            account["firstname"] = "REDACTED"
        if "lastname" in account:
            account["lastname"] = "REDACTED"
        path = fixtures_dir / "api_football_status.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        console.print(f"  ✅ {path.name}")
    except Exception as e:
        console.print(f"  ❌ api_football_status.json: {e}")

    try:
        r = await client.get(
            "https://v3.football.api-sports.io/fixtures",
            headers={"x-apisports-key": settings.api_football_key},
            params={"league": 39, "season": 2024},
            timeout=20,
        )
        data = r.json()
        if "response" in data and isinstance(data["response"], list):
            data["response"] = data["response"][:5]
        path = fixtures_dir / "api_football_epl_fixtures_sample.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        count = len(data.get("response", []))
        errors = data.get("errors", {})
        if errors:
            console.print(f"  ⚠️  {path.name} — API errors: {errors}")
        else:
            console.print(f"  ✅ {path.name} ({count} fixtures)")
    except Exception as e:
        console.print(f"  ❌ api_football_epl_fixtures_sample.json: {e}")

    console.print(f"\n  Fixtures guardados en: [cyan]{fixtures_dir}[/cyan]")


# ---------------------------------------------------------------------------
# Runner principal
# ---------------------------------------------------------------------------


async def run_healthcheck(capture: bool = False) -> bool:
    """Ejecuta todos los checks. Retorna True si todos pasan."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results: tuple[CheckResult, ...] = await asyncio.gather(
            check_odds_api(client),
            check_api_football(client),
            check_telegram(client),
            check_google_sheets(),
            check_healthchecks(client),
        )

        if capture:
            await capture_fixtures(client)

    # Mostrar tabla
    table = Table(title="Betting Bot — Health Check", show_lines=True)
    table.add_column("Servicio", style="bold")
    table.add_column("Estado", justify="center")
    table.add_column("Detalle")
    table.add_column("Extra")

    all_ok = True
    for r in results:
        status = "[green]✅ OK[/green]" if r.ok else "[red]❌ FAIL[/red]"
        extra = "  ".join(f"{k}: {v}" for k, v in r.extra.items())
        table.add_row(r.name, status, r.detail, extra)
        if not r.ok:
            all_ok = False

    console.print()
    console.print(table)
    console.print()

    if all_ok:
        console.print("[bold green]✅ Todas las APIs conectadas.[/bold green]")
    else:
        console.print("[bold red]❌ Algunos checks fallaron. Revisar detalle arriba.[/bold red]")

    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica conectividad con servicios externos.")
    parser.add_argument(
        "--capture-fixtures",
        action="store_true",
        help="Captura respuestas reales de APIs a tests/fixtures/ (consume quota).",
    )
    args = parser.parse_args()

    try:
        get_settings()
    except ValidationError as e:
        console.print("[bold red]❌ Error de configuración (.env):[/bold red]")
        for err in e.errors():
            field_name = ".".join(str(x) for x in err["loc"])
            console.print(f"  • [yellow]{field_name}[/yellow]: {err['msg']}")
        console.print("\nRevisá tu archivo [cyan].env[/cyan] contra [cyan].env.example[/cyan].")
        sys.exit(1)

    ok = asyncio.run(run_healthcheck(capture=args.capture_fixtures))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
