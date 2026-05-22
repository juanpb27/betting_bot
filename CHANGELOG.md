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

---

## [2026-05-21] — Sesión 5: Etapa 3 completada (ingesta con contratos)

### Hecho
- `ingestion/schemas.py`: contratos Pydantic de the-odds-api (`OddsApiEvent` y anidados) y api-football (`ApiFootballResponse`/`ApiFootballFixture` y anidados), validados contra los fixtures JSON reales.
- `ingestion/_http.py`: `request_with_retries()` (reintentos con backoff ante red/429/5xx) + DTO `QuotaInfo`.
- `ingestion/odds.py`: `OddsApiClient` async; parsea la cuota de los headers `x-requests-remaining`/`x-requests-used`.
- `ingestion/fixtures.py`: `ApiFootballClient` async; inspecciona `errors` del body (api-football devuelve HTTP 200 con error). `current_season()`.
- `ingestion/normalizer.py`: `normalize_team_name()` (NFKD→ASCII, sufijos de club) y `match_events()` — cruce difuso con rapidfuzz, ventana de 6h, circuit breaker bajo 90% de confianza.
- `cli/run_pipeline.py`: `run_ingestion()` orquesta fetch fixtures+odds en paralelo → match → upsert de `events` → `odds_snapshots` → `api_quota_log`. CLI `--ingest-only` con click. Aísla el fallo de una liga (no tumba la corrida de las demás) y cuenta los outcomes h2h no mapeados.
- `persistence/repo.py`: `QuotaRepo`, `EventRepo.get_by_api_football_id`/`update`. `yaml_config.py`: `LeagueConfig`, `load_active_leagues`, `load_odds_bookmakers`.
- Tests: `test_schemas.py`, `test_normalizer.py`, `test_ingestion_clients.py` (mocks con `httpx.MockTransport`), `test_run_pipeline.py`, `test_ingestion.py` (integración). `tests/factories.py` extendido con builders de modelos Pydantic y `load_fixture_json`. **73 tests unitarios + 3 de integración**, ruff y mypy limpios.
- Revisión de tech lead aplicada: `run_ingestion` aísla el fallo por liga — un error de API en una liga (key inválida, API caída, respuesta inesperada) ya no aborta la corrida de las demás; se registra en `IngestionResult.leagues_failed` y se sigue. Más tests del caso de fallo aislado, outcome no mapeado y confianza empatada.

### Decisiones tomadas
- Solo mercado `h2h` en esta etapa: el schema deja `point`/`line` opcional, sumar totals/btts/spreads después no rompe contratos. Se prueba el pipeline completo sobre un mercado antes de ampliar.
- Desarrollo en plan free: todo se construye y testea con fixtures+mocks. Los tests de integración corren contra las APIs reales (the-odds-api free da odds; api-football free da fixtures de 2024) — verificados, pasan.
- Tests de integración excluidos de la corrida normal vía `addopts = -m 'not integration'`; se corren con `uv run pytest -m integration`.
- `api_quota_log` queda con `requests_*` en `None` para api-football: `/fixtures` no expone la cuota en headers usables; no se gasta un `/status` extra.
- Upsert de `events` por `odds_api_id` en el orquestador (no un upsert genérico en el repo). Los `odds_snapshots` se acumulan por diseño (serie temporal de cuotas).

### Deuda técnica
- **`structlog` no montado.** El pipeline (`run_ingestion`/`run_pipeline`) usa `click.echo` para el resumen; no hay logs estructurados JSON ni `request_id` por corrida, que CLAUDE.md exige para el pipeline. Hoy el detalle de ligas con fallo y eventos sin match queda visible en el resumen del CLI. Pendiente: montar structlog (processors, `request_id`, JSON renderer) — conviene hacerlo junto al deploy a systemd (Etapa 8), cuando los logs van a journald.
- **`operation_log` no se popula.** Eventos sin match y ligas con fallo se cuentan en `IngestionResult` y se muestran en el resumen, pero no se escriben filas en la tabla `operation_log`. Pendiente para la misma tarea de observabilidad que structlog.

### Siguiente sesión
- Etapa 4: pricing — `pricing/devigging.py` (Shin con `brentq` + multiplicativo), `pricing/value.py`, `pricing/kelly.py`, con tests exhaustivos (TDD).

---

## [2026-05-22] — Sesión 6: Etapa 4 completada (pricing)

### Hecho
- `pricing/devigging.py`: `devig_multiplicative()` y `devig_shin()` (con `scipy.optimize.brentq`). Contrato `sum(fair)==1` ± 1e-8 sin re-normalización.
- `pricing/kelly.py`: `kelly_fraction()` y `calculate_stake()` (cap, floor, `rounding_unit`).
- `pricing/value.py`: `ValueAssessment` (frozen dataclass) + `assess_value()` (delega a `kelly.py`).
- `pricing/picks.py`: `generate_picks_for_event()` — orquestador que toma snapshots + configs, aplica `quality_gates` (require sharp quoted, min comparison books), de-vigga el sharp, evalúa cada outcome contra la mejor cuota de comparación, y devuelve `Pick`s desconectados.
- `yaml_config.py` extendido con `MarketConfig`/`StakingConfig`/`QualityGates` + loaders `load_active_markets`, `load_staking_config`, `load_quality_gates`, `load_sharp_reference_key`, `load_comparison_book_keys`.
- `tests/factories.py`: `build_event()` ahora asigna `id` vía `new_id()` (los tests sin session necesitan PK; el default del modelo solo se aplica en flush). Nuevo `build_odds_snapshot()`.
- `DESIGN.md §3`: nota explícita del snapshot único de bankroll por corrida.
- `config/thresholds.yaml`: comentarios marcando qué claves consume el código hoy (`[usado]`) vs cuáles son para etapas posteriores (`[pendiente]`).
- 128 tests unitarios + 3 de integración, ruff y mypy limpios, **coverage de `pricing/` 96%** (target del RUNBOOK alcanzado).

### Revisión pre-commit (matemático + back-eng + TL) y fixes aplicados
- **Resuelta la discrepancia de Shin en DESIGN.md §2.** Veredicto del experto: la **fórmula está correcta** (`B = Σπ` es la formulación canónica de Buchdahl / Štrumbelj 2014 / Shin 1993). Lo invertido era la **descripción**: Shin asigna MÁS prob al favorito (no menos), corrigiendo el favorite-longshot bias del retail. DESIGN.md §2 y el test fueron corregidos; agregado `test_devig_shin_corrects_favorite_longshot_bias` con fuentes citadas.
- **Bugs de borde corregidos en `kelly.py`:** guard `decimal_odds <= 1.0` y `p <= 0.0` para evitar `ZeroDivisionError` en cuotas degeneradas.
- **`value.py` — `min_odds_for_value` ahora es `(1 + min_ev) / p_real`** (cuota que dispara `has_value=True` con el `min_ev` configurado), no el breakeven `1/p`. Es el número útil operativamente. Guard agregado para `p_real <= 0`.
- **`picks.py` — tie-break determinista** en mejor cuota de comparación: `(price, bookmaker_key)` en vez de orden de iteración, así dos corridas con los mismos snapshots producen el mismo `comparison_book`.
- **`picks.py` — `bankroll <= 0` corta antes** de evaluar mercados (early return).
- **`picks.py` — método de-vigging desconocido ahora `raise ValueError`** en vez de `return []` silencioso (typo en `markets.yaml` se enteraba nadie). Test actualizado a `pytest.raises`.

### Decisiones tomadas
- **Bankroll snapshot único por corrida.** Una lectura de `ledger.get_total_balance()` al inicio; se reusa para todos los picks de la corrida y se persiste en `Pick.bankroll_at_generation`. Más simple, determinístico, sin races contra `/deposit` vía Telegram.
- **Solo `h2h` en esta etapa** (heredado de Etapa 3). El método `multiplicative` está implementado y testeado (lo usará totals/btts/spreads cuando se sumen).
- **No se montó la integración con `run_pipeline.py`** (modo `--full`) — corresponde a Etapa 5 junto con persistencia (`PickRepo.create`) y delivery a Telegram. `generate_picks_for_event` devuelve `Pick` objects desconectados, listos para que Etapa 5 los persista.
- **Coverage de `pricing/` 95%.** Las 5 líneas sin cubrir son ramas defensivas de error (`brentq` no bracketea, invariante Shin violado, `except` del try Shin en picks) que requieren mocking para ejercitar. Aceptable.

### Deuda técnica (heredada + reiterada + nueva tras review)
**Heredada / reiterada:**
- `structlog` no montado y `operation_log` no se popula — para Etapa 8 (deploy + observabilidad).
- Wiring de pricing en `run_pipeline.py --full` — Etapa 5.
- Delivery a Telegram, persistencia de `Pick`s vía `PickRepo.create` — Etapa 5.
- `SystemState.is_paused` no chequeado por el pipeline ni `last_pipeline_run_at` se actualiza — Etapa 5/7.
- Test de TZ round-trip (`commence_time` con offset no-UTC) — flagged por TL, defer.
- `cli/heartbeat.py`, `cli/telegram_listener.py` listados en CLAUDE.md pero ausentes — Etapa 5/8.

**Nueva — descubierta por property-based testing (hypothesis):**
- **Shin `brentq` no bracketea con overrounds extremos** (B > ~1.5, ej. `[2.0, 2.0, 1.0625]`). El bracket actual `[eps, 0.5 - eps]` asume `z < 0.5`, pero para overrounds patológicos el `z` real está fuera. No afecta producción (Pinnacle ~1.02, casas EU <1.10) pero el `ValueError` actual es genérico. Fix: ampliar bracket a `[eps, 0.99 - eps]` y/o docstring que aclare el supuesto. Los property tests filtran a `B ≤ 1.20` con `assume()` hasta que se resuelva.

**Nueva — diferida a Etapa 5 (consenso TL/back-eng):**
- **Staleness gate** en `picks.py`: filtrar snapshots con `captured_at` viejo (>`max_odds_age_minutes` de `thresholds.yaml`, ya declarado como `[pendiente]`). Hoy se usan tal cual.
- **Dedup de snapshots por `captured_at`**: si llegan dos snapshots de la misma casa+outcome (por reintentos de ingestión), `picks.py` queda con el orden de iteración. Debe quedarse con el más reciente.
- **Logging del orchestrator**: por qué *no* se generó pick (sharp ausente, gates fallaron, EV bajo, Shin no convergió). Va con structlog en Etapa 5.
- **`request_id` propagado** a `generate_picks_for_event` y a los `Pick`s — Etapa 5 lo va a necesitar para correlacionar corridas.
- **Aplicación de `max_picks_per_day` y `max_stake_per_event`** (ya en `bankroll.yaml`): el orchestrator es por-evento, los caps son globales. El caller (Etapa 5) debe leerlos antes de iterar eventos.
- **Persistencia de `z` (sharp_overround)** en `Pick`: hoy `picks.py` descarta el `z` que devuelve `devig_shin`. Es información analítica gratis (mide liquidez del sharp). Requiere migración de DB → mejor sumarlo *antes* de Etapa 5.
- **Política de idempotencia explícita** en `PickRepo.create` ante duplicados: ¿raise o silent skip? El índice unique cubre la DB, falta la decisión a nivel API.
- **Cacheo consistente en `yaml_config`**: `load_book_codes` usa `lru_cache`, los 5 loaders nuevos no. Unificar criterio.
- **`MarketConfig.outcomes` a `tuple[str, ...]`**: hoy es `list` mutable dentro de un `@dataclass(frozen=True)`.
- **Validación de rangos en `StakingConfig`** (`kelly_divisor > 0`, `0 < cap_pct ≤ 1`, etc.): hoy se valida implícitamente en cada llamada.
- **Banker's rounding en `calculate_stake`** está documentado pero conviene validar contra preferencia del usuario (`round-half-up` con `decimal`).
- **Tests faltantes recomendados por el matemático**: monotonía (favorito → longshot decreciente), Shin con `z` analítico conocido, property-based con `hypothesis` para `sum=1` en muestras random, idempotencia del multiplicativo, dirección de Shin para 2 vías.
- **Test integración del bankroll snapshot**: simular `/deposit` durante una corrida y verificar que los picks usan el bankroll inicial.

### Siguiente sesión
- Etapa 5: cableado pipeline → persistencia → Telegram. `run_pipeline.py --full` debe: ingerir (ya hecho) → llamar a `generate_picks_for_event` por cada evento → persistir los picks vía `PickRepo.create` → notificar vía Telegram con el formato de DESIGN.md §6. Chequear `SystemState.is_paused` al arrancar.
