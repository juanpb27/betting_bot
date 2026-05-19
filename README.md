# Betting Bot

Sistema automatizado de detección de value bets en fútbol. Cruza probabilidades reales (calculadas a partir de Pinnacle de-vigged) contra cuotas de casas blandas EU, notifica picks vía Telegram y trackea todo en Google Sheets + SQLite.

El usuario apuesta manualmente en casas colombianas (BetPlay, Codere, Rushbet, bwin).

## Documentación

- [CLAUDE.md](CLAUDE.md) — Contexto del proyecto, arquitectura, reglas de desarrollo. **Leer primero.**
- [DESIGN.md](DESIGN.md) — Modelo de datos, código de pricing/de-vigging/Kelly, configs YAML, formato Telegram.
- [RUNBOOK.md](RUNBOOK.md) — Setup de cuentas, etapas de implementación, deployment, troubleshooting.
- [CHANGELOG.md](CHANGELOG.md) — Registro de sesiones y decisiones.

## Quickstart

```bash
# 1. Instalar deps y crear venv
uv sync

# 2. Configurar entorno
cp .env.example .env
# Llenar los valores siguiendo RUNBOOK.md sección 1.

# 3. Crear DB
uv run alembic upgrade head

# 4. Verificar conectividad
uv run python -m betting_bot.cli.healthcheck
```

## Stack

Python 3.12 · uv · SQLite (WAL) · SQLAlchemy + Alembic · httpx · python-telegram-bot · gspread · scipy · structlog · systemd timers.
