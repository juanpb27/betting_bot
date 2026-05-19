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

### Siguiente sesión
- Etapa 1 del plan en `RUNBOOK.md` sección 3: crear cuentas externas, copiar `.env.example` → `.env`, llenar valores, implementar `config.py` y `cli/healthcheck.py`, capturar fixtures JSON reales.
