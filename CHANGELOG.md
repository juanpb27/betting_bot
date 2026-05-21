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

---

## [2026-05-20] — Sesión 2: Etapa 2 completada

### Hecho
- `src/betting_bot/ids.py`: `new_id()` — UUID v4 como ID de entidades cross-system.
- `src/betting_bot/persistence/models.py`: 8 modelos SQLAlchemy 2.0 declarativos (`Event`, `OddsSnapshot`, `Pick`, `BankrollMovement`, `BankrollBookSnapshot`, `SystemState`, `ApiQuotaLog`, `OperationLog`) según DESIGN.md sección 1.
- `src/betting_bot/persistence/db.py`: engine lazy con `lru_cache`, `session_scope()` transaccional, listener de PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`), `resolve_database_url()` (resuelve paths relativos a la raíz del repo).
- `src/betting_bot/yaml_config.py`: loader YAML mínimo + `load_book_codes()` (lee `config/books.yaml`).
- `src/betting_bot/bankroll/ledger.py`: `BankrollLedger` — `record_deposit/withdrawal/bet_stake/bet_payout/adjustment`, `get_balance_by_book`, `get_total_balance`. Valida `book_code` y signo de montos.
- `src/betting_bot/persistence/repo.py`: `EventRepo`, `OddsRepo`, `PickRepo` (con `create` idempotente), `SystemStateRepo` (singleton get-or-create).
- Alembic configurado: `env.py` conectado a `Base.metadata` y a `DATABASE_URL` de `.env`, `render_as_batch=True`. Migración `6365f10cee0c_initial_schema` autogenerada y aplicada → `data/betting_bot.db` con las 8 tablas; `downgrade base` revierte limpio.
- Tests: `tests/conftest.py` (DB SQLite en memoria), `test_ledger.py` (18), `test_repos.py` (14). **32 tests passing**, ruff y mypy limpios, **coverage del ledger 100%**.

### Decisiones tomadas
- IDs con UUID **v4** en lugar del v7 de DESIGN.md: a la escala del proyecto (miles de filas) la ordenabilidad de v7 no aporta nada — la ordenabilidad ya la dan las columnas de timestamp dedicadas — y v4 evita código propio y la migración a Python 3.14.
- Índice de idempotencia de `picks`: **dos índices únicos parciales** (`idx_picks_unique_with_line` / `idx_picks_unique_no_line` con `sqlite_where`) en lugar del único índice con `COALESCE(line, -999)` de DESIGN.md. SQLite no refleja índices de expresión y Alembic no los autogenera; los parciales sobre columnas planas sí, dan la misma garantía y son portables a Postgres.
- Fila singleton de `system_state` no se siembra en la migración (Alembic no genera `INSERT`s de data): se asegura vía `SystemStateRepo.get()` con get-or-create idempotente.
- Repos y ledger operan sobre una `Session` inyectada y no commitean — el llamador controla la transacción.
- Árbol `migrations/` excluido de ruff y mypy: son artefactos generados por Alembic.

---

## [2026-05-20] — Sesión 3: refactor currency-neutral

### Hecho
- Codebase agnóstico de moneda: un mismo código desplegable en COP o USD, cada deployment con su DB y su moneda única (sin mezcla de monedas).
- Renombradas las 11 columnas de plata quitando el sufijo `_cop` (`amount`, `balance`, `pnl`, `stake_recommended`, `bankroll_at_generation`, `actual_stake`, `deposits_today`, `withdrawals_today`, `stakes_today`, `payouts_today`, `pnl_today`) en `models.py`, `ledger.py` y sus tests.
- `config.py`: nuevo setting `currency` (default `COP`, validado contra `{COP, USD}`); `.env.example` con `CURRENCY=COP`.
- `config/bankroll.yaml`: `max_stake_per_event_cop` → `max_stake_per_event`; nuevo `stake_rounding_unit: 1000` (granularidad de redondeo del stake, per-deployment).
- `PickRepo._find_duplicate`: reemplazado `func.coalesce(line, -999)` por branch `is_(None)` — mismo criterio que los dos índices parciales, sin número mágico.
- Migración inicial regenerada (`5c188d55ae7c`) con los nombres neutros; `data/betting_bot.db` recreada.
- DESIGN.md, CLAUDE.md, RUNBOOK.md actualizados: schema, queries de bankroll, `calculate_stake` (param `rounding_unit` en vez de `-3` hardcodeado), formato Telegram, convención de montos.
- 32 tests passing, ruff y mypy limpios, coverage del ledger 100%.

### Decisiones tomadas
- Montos enteros sin decimales también en USD (dólares enteros): coherente con que COP ya se redondea a miles; un sistema de tracking no necesita precisión al centavo.
- `currency` es solo etiqueta de presentación (formato Telegram/Sheets, Etapa 5/6) + base del redondeo de stake. La lógica del bot (Kelly, EV, de-vigging, sumas del ledger) es agnóstica de moneda.
- `config/*.yaml` son per-deployment, no compartidos: un deployment USD tendría otras casas y otros montos.

### Siguiente sesión
- Etapa 3: ingesta con contratos — `ingestion/schemas.py` (Pydantic), `ingestion/fixtures.py`, `ingestion/odds.py`, `ingestion/normalizer.py`, tests con los fixtures JSON capturados en Etapa 1.

---

## [2026-05-20] — Sesión 4: hardening de tests (post review de tech lead)

### Hecho
- Review de la suite por el tech lead: los 32 tests pasaban pero las garantías duras (índices únicos, FKs, CHECKs) no se ejercían — falsa sensación de seguridad.
- `bankroll/ledger.py`: el ledger ahora rechaza cualquier movimiento que deje una casa en saldo negativo (nuevo `_book_balance`, check en `_record`). 5 tests TDD nuevos (withdrawal/stake/adjustment que sobrepasan el saldo, retiro a cero exacto permitido).
- `tests/unit/test_schema_constraints.py` (7 tests): insertan filas directo por la sesión para probar que la DB rechaza picks duplicados (los dos índices únicos parciales), FKs huérfanas (`PRAGMA foreign_keys=ON`) y `movement_type`/`status`/singleton inválidos (CHECKs).
- `tests/integration/test_schema.py`: test de drift — `alembic check` sobre una DB temporal falla si los modelos divergen de las migraciones (los tests unitarios usan `create_all()` y no lo detectarían).
- `tests/factories.py`: `build_event`/`build_pick` — elimina la duplicación de helpers entre archivos de test.
- 45 tests passing (32 → 45), ruff y mypy limpios, coverage del ledger 100%.

### Decisiones tomadas
- El ledger rechaza saldo negativo en cualquier casa: no se puede tener saldo negativo en una casa de apuestas real, así que el ledger tampoco lo permite. Aplica a withdrawal, bet_stake y adjustment negativo.

### Siguiente sesión
- Etapa 3: ingesta con contratos — `ingestion/schemas.py` (Pydantic), `ingestion/fixtures.py`, `ingestion/odds.py`, `ingestion/normalizer.py`, tests con los fixtures JSON capturados en Etapa 1.
