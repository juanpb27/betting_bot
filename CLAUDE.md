# Betting Bot — Project Context for Claude Code

> Este archivo le da contexto a Claude Code (y a cualquier desarrollador nuevo) sobre QUÉ es el proyecto, POR QUÉ existe, y CÓMO trabajar en él. Léelo siempre al iniciar una sesión.

## TL;DR

Sistema automatizado que detecta apuestas de valor (value bets) en fútbol cruzando probabilidades reales (calculadas a partir de Pinnacle de-vigged) contra cuotas de casas de apuestas blandas EU. Notifica al usuario vía Telegram con la información mínima necesaria para decidir, y permite tracking completo en Google Sheets para análisis posterior y aprendizaje del sesgo de casas colombianas (donde se apuesta realmente).


## Contexto del dominio

### Qué es una apuesta de valor

Existe valor cuando la probabilidad real estimada de un evento es mayor que la probabilidad implícita en la cuota ofrecida. Fórmula:

```
EV = (p_real × (cuota - 1)) - (1 - p_real)
```

Si EV > 0, hay valor. Operamos picks con EV mínimo de 2.5–3% según el mercado.

### Por qué Pinnacle como referencia

Pinnacle es un market maker con margen ultra bajo (~2%) que no limita ganadores. Su línea se considera la más cercana a "probabilidad real". Tomamos sus cuotas, le quitamos el margen ("de-vigging") y obtenemos `p_real`. NO apostamos en Pinnacle (no opera en Colombia); usamos sus cuotas SOLO como cálculo de referencia.

### Por qué casas EU como señal

Las casas blandas europeas (Bet365 EU, Betsson, William Hill, 888sport) suelen tener cuotas similares a las casas blandas colombianas. Cuando alguna casa EU paga sustancialmente más que Pinnacle (con margen quitado), hay señal de que el mercado retail está desfasado y probablemente las casas colombianas también. El usuario verifica manualmente en su casa local antes de apostar.

### Por qué no se apuesta directamente desde el sistema

(a) Las casas colombianas no tienen APIs públicas. (b) La automatización completa cruzaría líneas legales en jurisdicción colombiana. (c) El usuario quiere control humano final por seguridad. El sistema sugiere, el usuario ejecuta.

## Arquitectura de alto nivel

```
┌─────────────────────────────────────────────────────────────┐
│  SCHEDULER (systemd timers en el servidor)                  │
│  - 05:00 COL: corrida principal del día                     │
│  - 09:00/13:00/17:00/20:00 COL: refresh                     │
│  - cada 30 min: settlement de partidos terminados           │
│  - domingo 22:00: reporte semanal                           │
│  - cada hora: dead-man's switch ping a healthchecks.io      │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
│  FIXTURES    │   │  ODDS        │   │  STATS (fase 2)  │
│  api-football│   │  the-odds-api│   │  api-football    │
└──────────────┘   └──────────────┘   └──────────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  NORMALIZER / MATCHER                │
        │  - rapidfuzz para nombres equipos    │
        │  - validación cruzada fecha+liga     │
        │  - circuit breaker si confianza <90% │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  VALUE ENGINE                        │
        │  - De-vigging Pinnacle               │
        │    · Shin's method para 1X2          │
        │    · Multiplicativo para 2-vías      │
        │  - Comparación contra casas EU       │
        │  - Cálculo EV y cuota mínima         │
        │  - Filtro por threshold              │
        │  - Kelly/4 con cap 3% / floor 0.3%   │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  PERSISTENCIA (SQLite + WAL mode)    │
        │  events, odds_snapshots, picks,      │
        │  bankroll_movements,                 │
        │  bankroll_book_snapshots,            │
        │  api_quota_log, system_state,        │
        │  operation_log                       │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  DELIVERY                            │
        │  - Telegram bot (python-telegram-bot)│
        │  - Google Sheets (gspread)           │
        └──────────────────────────────────────┘
                            ▼
        ┌──────────────────────────────────────┐
        │  SETTLEMENT                          │
        │  - Polling resultados                │
        │  - Cálculo P&L real (cuota usuario)  │
        │  - Update bankroll dinámico          │
        │  - CLV, ROI, hit rate, edge realizado│
        └──────────────────────────────────────┘
```

## Decisiones técnicas y su justificación

### Stack

| Decisión | Justificación |
|---|---|
| Python 3.12 | Estándar moderno, soporte completo de librerías necesarias |
| uv | 10–100× más rápido que pip/poetry, manejo de Python integrado |
| SQLite + WAL mode | Single writer, sin ops, portable, suficiente para escala esperada |
| Alembic | Migraciones versionadas, prepara migración eventual a Postgres |
| python-telegram-bot v21+ | Maduro, async nativo, soporta inline keyboards |
| gspread + google-auth | Estándar para Google Sheets desde Python con service account |
| httpx | Cliente HTTP moderno, async, mejor que requests para nuestro caso |
| pytest + pytest-asyncio | Estándar de testing en Python moderno |
| rapidfuzz | Mejor performance que fuzzywuzzy para matching de equipos |
| scipy | Solver `brentq` para Shin's method; root-finder testeado y robusto |
| systemd timers | Más robusto que cron, mejor logging, dependencias entre tareas |
| ruff | Linter+formatter ultra rápido, reemplaza black+flake8+isort |
| healthchecks.io | Dead-man's switch externo gratuito (free tier suficiente) |

### Anti-decisiones (qué NO hacemos y por qué)

- **No Docker**: para single-tenant en Vultr no aporta valor. Más capas de debugging, más volúmenes, más complejidad para nada. systemd + venv son suficientes.
- **No modos `dry_run` / `paper_trading` / `low_stake` en código**: durante desarrollo se usa una DB de juguete con bankroll ficticio cargado vía `/deposit`. Al pasar a operación real: `rm data/betting_bot.db`, vacía Sheet, registra deposits reales vía Telegram, arranca limpio. Cero código de modo, cero columna `is_paper`, cero ramas en tests por staging.
- **No LLM en fase 1**: el cálculo de EV y de-vigging es matemática determinista. Meter LLM introduce no-determinismo en una operación auditable.
- **No scraping de casas colombianas**: frágil, prohibido en T&C de varias casas, alto mantenimiento. Aprendemos sesgo registrando manualmente en Sheets.
- **No Postgres todavía**: SQLite cubre 12–18 meses sin problemas. Migramos si data crece o necesitamos multi-writer.
- **No Cowork**: no es plataforma para sistemas autónomos que mueven dinero. Falta observabilidad, versionado, tests.
- **No backtest en fase 1**: requiere histórico de odds que las APIs gratis no dan. En fase 2.
- **No mercados de córners/tarjetas en fase 1**: APIs lo cubren peor, complejidad extra sin payoff claro.
- **No `transfer` entre casas como tipo de movimiento**: se modela como `withdrawal` en una + `deposit` en otra. Menos tipos, menos código.

## Mercados y ligas

### Mercados (fase 1)
- `h2h` (1X2): de-vigging con Shin's method, EV mínimo 3%.
- `totals` (Over/Under 2.5 goles): multiplicativo, EV mínimo 2.5%.
- `btts` (Both Teams To Score): multiplicativo, EV mínimo 2.5%.
- `spreads` (Asian Handicap): multiplicativo, EV mínimo 2.5%.

### Ligas (fase 1)
- Premier League, LaLiga, Serie A, Bundesliga, Champions League, Europa League.
- FIFA World Cup 2026 — `active: false` por defecto, se activa manualmente en `config/leagues.yaml` cuando empiece el torneo.

## Bankroll y gestión

El bankroll **NO** se setea por variable de entorno. Vive en la DB como un ledger de movimientos (`bankroll_movements`). Cada depósito, retiro, stake apostado, payout cobrado, o ajuste manual es una fila. El saldo por casa en cualquier momento = `SUM(amount_cop) WHERE book_code = X`.

### Setup inicial del bankroll

La primera vez (o después de un reset en desarrollo) se registran deposits por casa vía Telegram:

```
/deposit betplay 750000
/deposit codere 625000
/deposit rushbet 625000
/deposit bwin 500000
```

### Movimientos durante operación

- **Recargar una casa**: `/deposit codere 200000`.
- **Retirar de una casa**: `/withdraw betplay 100000`.
- **Mover entre casas**: `/withdraw bwin 50000` + `/deposit rushbet 50000` (dos comandos, sin tipo "transfer").
- **Ajuste manual** (reconciliación, bonus expirado, error detectado): `/adjust betplay -25000 razón_libre`.
- **Stakes y payouts** se registran automáticamente en settlement; no hay comando manual.

### Comandos de consulta

- `/balance` → saldo total y per-casa.
- `/bankroll` → resumen completo: saldo, picks pendientes, P&L del día/semana/mes.

### Staking

- Kelly fraccional 1/4 con cap 3% y floor 0.3%, sobre el bankroll vivo (suma de saldos de todas las casas activas).
- `max_stake_per_event_cop` para evitar concentración (suma stakes por event_id).

### Ramp-up al iniciar producción

No hay modo "low_stake". Para apostar poco al inicio, simplemente depositás menos plata real en las casas (ej. 500k total repartido). Kelly automáticamente genera stakes pequeños proporcionales. Cuando crezca tu confianza, depositás más con `/deposit` y los stakes escalan solos.

### Cálculo de stake

```python
def kelly_fraction(p: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """Returns optimal stake as fraction of bankroll using fractional Kelly."""
    b = decimal_odds - 1
    q = 1 - p
    raw_kelly = (b * p - q) / b
    if raw_kelly <= 0:
        return 0.0
    return raw_kelly * fraction

def calculate_stake(bankroll: float, p_real: float, odds: float,
                    fraction: float = 0.25, cap: float = 0.03,
                    floor: float = 0.003) -> float:
    """Returns stake amount in COP, applying cap and floor."""
    f = kelly_fraction(p_real, odds, fraction)
    if f < floor:
        return 0.0  # skip pick
    f = min(f, cap)
    return round(bankroll * f, -3)  # round to nearest thousand COP
```

## Métricas a trackear

| Métrica | Qué mide | Cuándo confiar |
|---|---|---|
| CLV (Closing Line Value) | Cuán mejor fue tu cuota vs cuota de cierre Pinnacle | 50–100 picks |
| ROI / Yield | Ganancia / total apostado | 500+ picks |
| Hit rate por mercado | % aciertos por mercado y rango de cuotas | 100+ por bucket |
| Edge predicho vs realizado | ¿El EV reportado se materializa? | 200+ picks |
| Drawdown máximo | Peor caída desde peak | Monitoreo continuo |
| Distribución de stakes | Concentración del riesgo | Monitoreo continuo |

## Reglas de comportamiento del sistema

1. **Circuit breakers (internos, via `system_state`)** — controlan si el bot **quiere** apostar:
   - `system_state` es una tabla singleton con flags `is_paused`, `paused_reason`, `paused_at`, `last_pipeline_run_at`.
   - El pipeline lee `is_paused` al inicio; si está `true`, hace exit limpio sin generar picks.
   - Casos que setean `is_paused=true` automáticamente:
     - Bankroll cae >5% en una semana.
     - API falla 3 veces consecutivas.
   - Casos manuales: usuario manda `/pause [razón]` desde Telegram.
   - Reanudar: usuario manda `/resume` (limpia `is_paused`).
   - Otras condiciones que **NO** pausan pero sí alertan: matching <90% (skip ese pick), cuota vieja >30min (skip), quota API <20% del plan (warning a Telegram).

2. **Dead-man's switch externo (healthchecks.io)** — verifica si el bot **está vivo**:
   - Cubre fallas que `system_state` NO puede detectar: Vultr cayó, systemd desactivado, proceso zombie, lock file colgado, DB corrupta. Si la app no corre, la app no puede pausarse a sí misma — necesitás un watchdog externo.
   - Cada corrida exitosa del pipeline hace `curl $HEALTHCHECKS_URL`.
   - Además, `betting-bot-heartbeat.timer` pinguea cada hora.
   - Si healthchecks.io no recibe ping en 8h, **él** dispara alerta (email + integración con Telegram).
   - Es complementario a `system_state`, no redundante: uno dice "estoy decidido a no apostar", el otro dice "no sé si seguís vivo".

3. **Idempotencia**:
   - Generar pick es idempotente por (event_id, market_key, outcome, line, generated_date).
   - `generated_date` es columna DATE explícita, calculada en Python al insertar usando la TZ del proyecto (`settings.timezone`). No se usa `date(generated_at)` en SQL porque aplicaría TZ UTC y rompería en bordes (pick generado a las 23:30 hora local = día siguiente UTC).
   - Re-correr el pipeline el mismo día no duplica picks.

4. **Trazabilidad**:
   - Todo snapshot de odds queda guardado.
   - Cada pick referencia los snapshots que lo generaron.
   - Logs estructurados (JSON) con request_id por corrida.
   - `bankroll_movements` es el ledger de verdad; `bankroll_book_snapshots` es cache diario calculado.

5. **No-go silencioso**:
   - Si no hay picks con EV+, el pipeline corre y registra "no picks" en log + Sheets. NO manda Telegram (evitar spam).
   - Reporte semanal sí va siempre, incluso si la semana fue sin picks.

## Estructura del repo

```
betting_bot/
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── README.md
├── CLAUDE.md
├── DESIGN.md
├── RUNBOOK.md
├── CHANGELOG.md
├── config/
│   ├── leagues.yaml
│   ├── markets.yaml
│   ├── books.yaml
│   ├── thresholds.yaml
│   └── bankroll.yaml
├── src/
│   └── betting_bot/
│       ├── __init__.py
│       ├── config.py
│       ├── ingestion/
│       │   ├── fixtures.py
│       │   ├── odds.py
│       │   ├── normalizer.py
│       │   └── schemas.py        # Pydantic models de respuestas API
│       ├── pricing/
│       │   ├── devigging.py
│       │   ├── value.py
│       │   └── kelly.py
│       ├── persistence/
│       │   ├── models.py
│       │   ├── repo.py
│       │   └── migrations/
│       ├── bankroll/
│       │   ├── ledger.py         # API sobre bankroll_movements
│       │   └── snapshots.py      # cálculo diario para bankroll_book_snapshots
│       ├── delivery/
│       │   ├── telegram_bot.py
│       │   ├── telegram_handlers.py
│       │   └── sheets_sync.py
│       ├── settlement/
│       │   └── settle.py
│       ├── analytics/
│       │   ├── metrics.py
│       │   └── reports.py
│       └── cli/
│           ├── run_pipeline.py
│           ├── settle.py
│           ├── reconcile.py
│           ├── weekly_report.py
│           ├── healthcheck.py
│           ├── heartbeat.py           # ping a healthchecks.io (lo dispara el timer)
│           └── telegram_listener.py   # proceso long-running del bot (lo dispara el .service)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/                 # JSON snapshots reales sanitizados de ambas APIs
├── notebooks/
└── ops/
    └── systemd/
        ├── betting-bot-pipeline.service
        ├── betting-bot-pipeline.timer
        ├── betting-bot-settle.service
        ├── betting-bot-settle.timer
        ├── betting-bot-telegram.service
        └── betting-bot-heartbeat.timer
```

## Prácticas de desarrollo (regla obligatoria)

Estas prácticas no son negociables. Son lo que separa este proyecto de un script que rompe en silencio y nos hace perder plata.

### Test-Driven Development (TDD) para lógica core

**Para módulos en `pricing/`, `ingestion/normalizer.py`, `ingestion/schemas.py`, `settlement/`, `bankroll/ledger.py`, y `analytics/metrics.py`**, el flujo es:

1. Escribir el test que define el comportamiento esperado.
2. Correr el test → debe fallar (rojo).
3. Implementar el código mínimo para que pase (verde).
4. Refactorizar manteniendo el test en verde.

Es porque el de-vigging, el cálculo de EV, el matching de equipos, el settlement y el ledger del bankroll son las partes donde un bug se traduce en pérdida monetaria directa.

Para módulos de I/O y orquestación (`cli/`, `delivery/`, scripts), se permite implementar primero y testear después con integration tests usando mocks.

### Contratos de APIs externas con Pydantic

Las respuestas de the-odds-api y api-football deben tener schemas Pydantic en `ingestion/schemas.py`. Si un campo cambia, los tests fallan. Sin esto, los cambios silenciosos de las APIs rompen prod sin aviso.

### Fixtures de tests basados en respuestas reales

`tests/fixtures/` contiene JSONs reales (sanitizados) capturados de ambas APIs. Los unit tests parsean estos fixtures, no objetos `Mock()` inventados. Cuando un test rompe contra fixtures reales, el problema es real.

### CHANGELOG.md actualizado al final de cada sesión

Después de completar cualquier tarea, agregar entrada a `CHANGELOG.md` siguiendo este formato:

```markdown
## [YYYY-MM-DD] — Sesión NN

### Hecho
- Cambio concreto 1 (archivo o módulo afectado).
- Cambio concreto 2.

### Decisiones tomadas
- Decisión X → razón breve.

### Siguiente sesión
- Próximo paso accionable.
```

Reglas:
- **Enfocar en lo hecho**, no en lo no decidido. Si algo se descartó deliberadamente, va en `Decisiones tomadas` con su razón, no en una sección aparte de "no scope".
- Sección de **deuda técnica solo si aparece deuda técnica real** (decisión tomada bajo presión que sabés que está mal). Scope deliberadamente diferido NO es deuda técnica — vive en el roadmap de DESIGN.md.
- Issues conocidos solo si son bugs sin resolver al cerrar sesión.

### Mensajes de commit en formato convencional

- `feat:` nueva funcionalidad.
- `fix:` corrección de bug.
- `refactor:` cambio de estructura sin cambio de comportamiento.
- `test:` agregar o modificar tests.
- `docs:` cambios en documentación.
- `chore:` mantenimiento.
- `perf:` mejora de performance.
- `ops:` cambios en infraestructura o deployment.

Ejemplo: `feat(pricing): implement Shin's method via brentq solver`.

### Justificar dependencias nuevas antes de instalar

Antes de agregar una librería:
1. ¿Se puede lograr con stdlib o con dependencias ya instaladas?
2. ¿La librería está mantenida (último commit <6 meses)?
3. ¿Cuál es el costo (peso, deps transitivas)?
4. ¿Hay alternativas más livianas?

### Logs estructurados, no `print()`

Usar `structlog`. Cada corrida del pipeline tiene un `request_id` que se propaga a todos los logs.

### Type hints obligatorios en funciones públicas

Usar `from __future__ import annotations` para evitar overhead en runtime.

### Migraciones de DB siempre con Alembic

Nunca DDL directo en código de producción.

## Reglas estrictas para Claude Code

1. **SIEMPRE leer CLAUDE.md, DESIGN.md, RUNBOOK.md y CHANGELOG.md al empezar sesión.**
2. **TDD para lógica core** (pricing, normalizer, schemas API, settlement, bankroll/ledger, analytics).
3. **No agregar dependencias sin justificar.**
4. **No tocar `pricing/` sin tests de regresión.**
5. **Logs estructurados siempre.**
6. **Type hints obligatorios** en funciones públicas.
8. **Migraciones DB siempre con Alembic.**
9. **Actualizar CHANGELOG.md al final de cada sesión.**
10. **Mensajes de commit en formato convencional.**
11. **Antes de cada acción significativa, mostrar el plan y pedir confirmación.**

## Comandos comunes

```bash
# Setup
uv sync
uv run alembic upgrade head

# Desarrollo
uv add <package>
uv add --dev <package>
uv run pytest
uv run pytest tests/unit/test_devigging.py -v
uv run pytest --cov=src/betting_bot
uv run ruff check .
uv run ruff format .
uv run mypy src/

# Ejecución
uv run python -m betting_bot.cli.run_pipeline
uv run python -m betting_bot.cli.run_pipeline --league soccer_epl
uv run python -m betting_bot.cli.settle
uv run python -m betting_bot.cli.weekly_report
uv run python -m betting_bot.cli.reconcile
uv run python -m betting_bot.cli.healthcheck

# DB
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head
uv run alembic downgrade -1

# Operación (servidor)
systemctl status betting-bot-pipeline.timer
journalctl -u betting-bot-pipeline.service -f
systemctl list-timers --all | grep betting-bot
```

## Variables de entorno

Ver `.env.example`. Las críticas:

- `API_FOOTBALL_KEY`
- `ODDS_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON_PATH`
- `GOOGLE_SHEET_ID`
- `DATABASE_URL` (default: `sqlite:///data/betting_bot.db`)
- `HEALTHCHECKS_URL` (URL única del check en healthchecks.io)
- `LOG_LEVEL` (default: `INFO`)
- `TIMEZONE` (default: `America/Bogota`)


## Flujo de transición pruebas → operación real

1. Durante desarrollo y validación: DB local `betting_bot.db`, bankroll ficticio cargado vía `/deposit` para simular.
2. Cuando el sistema esté validado (Shin convergiendo, CLV cercano a 0, sin bugs detectados en 50+ picks):
   - `rm data/betting_bot.db`.
   - `uv run alembic upgrade head` (DB limpia).
   - Registrar deposits **reales** vía `/deposit <book> <amount>` por cada casa donde tengas plata.
   - Listo: pipeline corre exactamente igual, pero con plata real.

## Fase actual

**Fase 1: MVP funcional pre-Mundial.** Setup completo, pipeline determinista, notificaciones a Telegram, tracking en Sheets, ledger de bankroll vía Telegram, transición controlada de dev a prod antes del Mundial.

**Fase 2 (post-Mundial):** Modelo Poisson/Dixon-Coles propio, mercados de córners/tarjetas, dashboard web, posible LLM para anotaciones cualitativas.

**Fase 3 (TBD):** Backtest histórico, optimización de thresholds basado en data acumulada, multi-deporte.
