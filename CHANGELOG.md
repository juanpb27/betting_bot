# Changelog

Todos los cambios significativos del proyecto se registran aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/).

## [2026-05-18] — Sesión 0: bootstrap del repositorio

### Hecho
- Documentación base: `CLAUDE.md` (contexto y reglas), `DESIGN.md` (modelo de datos, cálculos, configs), `RUNBOOK.md` (operación y etapas de implementación).
- Estructura del repositorio: `src/betting_bot/{ingestion,pricing,persistence,bankroll,delivery,settlement,analytics,cli}`, `tests/{unit,integration,fixtures}`, `config/`, `ops/systemd/`, `notebooks/`.
- `pyproject.toml` con dependencias declaradas (httpx, pydantic, sqlalchemy, alembic, rapidfuzz, scipy, python-telegram-bot, gspread, structlog, click, rich) y dev-deps (pytest, ruff, mypy).
- Configs YAML: `leagues.yaml`, `markets.yaml`, `books.yaml`, `bankroll.yaml`, `thresholds.yaml`.
- `.env.example` con todas las variables documentadas.
- `.gitignore` extendido con entradas del proyecto (DB, credenciales, backups, logs).

### Decisiones tomadas
- Bankroll vivo en DB (`bankroll_movements` como ledger), seteado vía Telegram con `/deposit`, sin `BANKROLL_INITIAL_COP` en `.env`.
- Sin Docker: systemd + venv directo en host. Menos capas de debug para single-tenant.
- Sin modos `dry_run`/`paper_trading`/`low_stake`: la distinción pruebas vs operación real se hace fuera del código (DB de juguete vs DB limpia con deposits reales).
- Shin's de-vigging con `scipy.optimize.brentq` (validado por revisión de tech lead) en vez de Newton-Raphson manual.
- Header de api-football: `x-apisports-key` (suscripción directa en api-sports.io), no `x-rapidapi-key`.
- `generated_date` calculada en Python con TZ del proyecto, no `date(generated_at)` en SQL.
- Comandos de bankroll vía Telegram en lugar de CLI: `/deposit`, `/withdraw`, `/adjust`, `/balance`, `/bankroll`.
- Plan de implementación en etapas numeradas (1–10), sin estimaciones de tiempo ni días de la semana.

---

## [2026-05-20] — Sesión 1: Etapa 1 completada

### Hecho
- `uv sync`: venv creado, 75 paquetes instalados (CPython 3.13.7).
- `src/betting_bot/config.py`: Pydantic Settings con validación de `log_level`, resolución de rutas relativas, singleton `get_settings()` cacheado con `lru_cache`.
- `src/betting_bot/cli/healthcheck.py`: check async de 5 servicios en paralelo (the-odds-api, api-football, Telegram, Google Sheets, healthchecks.io). Salida Rich con tabla. Exit code 0/1 según resultado. Flag `--capture-fixtures` para capturar JSONs reales.
- Fixtures reales capturados en `tests/fixtures/`: `odds_api_sports.json`, `odds_api_epl_h2h_sample.json` (3 eventos EPL), `api_football_status.json`, `api_football_epl_fixtures_sample.json` (5 fixtures EPL 2024).
- `uv run python -m betting_bot.cli.healthcheck`: 5/5 verde — todas las APIs conectadas.
- Revisión de tech lead aplicada (4 correcciones):
  - `check_healthchecks` ya no pinguea el check del usuario — solo valida formato de URL y reachability del host `hc-ping.com`. Un ping reactiva un check pausado y resetea el grace timer del dead-man's switch.
  - `check_api_football` inspecciona el campo `errors` del body y exige `account.email`: api-football devuelve HTTP 200 con errores, `raise_for_status()` no los detecta.
  - `check_telegram` chequea el campo `ok` del JSON (Telegram puede devolver `ok=false` con HTTP 200).
  - mypy pasa en strict: plugin `pydantic.mypy` agregado a `pyproject.toml`, anotación de tipo del `asyncio.gather` corregida, `# type: ignore` puntual en `from_service_account_file`.
- `capture_fixtures` redacta `firstname`/`lastname` además del email; fixture `api_football_status.json` sanitizado.
- `cli/healthcheck.py main()` captura `ValidationError` de Pydantic y reporta qué variable de `.env` falta, en vez de un traceback.

### Decisiones tomadas
- Fixtures de api-football con `season: 2024` (hardcodeado): el plan Free solo permite 2022–2024. En Etapa 3 (con plan Pro) se actualizan a temporada en curso.
- `check_google_sheets()` importa `gspread`/`google-auth` dentro de la función: evita import-time error si las credenciales no están; el error se reporta en la tabla de checks.
- El healthcheck nunca pinguea healthchecks.io: el ping real es exclusivo del final del pipeline y de `cli/heartbeat.py`. Verificar conectividad y pingear son acciones distintas que no deben mezclarse.

### Siguiente sesión
- Etapa 2: `persistence/models.py`, setup Alembic, primera migración, `persistence/repo.py`, `bankroll/ledger.py` con TDD.
