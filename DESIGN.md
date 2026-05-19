# Betting Bot — Design Document

> Especificaciones técnicas detalladas. Complementa CLAUDE.md (contexto general) y RUNBOOK.md (operación).

## 1. Modelo de datos

### Convenciones generales
- IDs internos: UUID v7 (sortable por tiempo) para entidades con identidad cross-system (`events`, `picks`). Bigint autoincrement para tablas internas (`odds_snapshots`, `bankroll_movements`, etc.).
- Timestamps: TIMESTAMP, almacenar en UTC, convertir a `America/Bogota` solo en presentación.
- Monedas: COP, integer.
- Cuotas decimales (no fraccionales ni americanas).
- SQLite con WAL mode habilitado.

### Esquema completo

```sql
-- Eventos (partidos)
CREATE TABLE events (
    id TEXT PRIMARY KEY,                    -- UUID v7
    odds_api_id TEXT UNIQUE,                -- id estable de the-odds-api
    api_football_id INTEGER,                -- id de api-football
    league_key TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    commence_time TIMESTAMP NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('scheduled', 'live', 'finished', 'cancelled', 'postponed')),
    home_score INTEGER,
    away_score INTEGER,
    home_goals_ht INTEGER,
    away_goals_ht INTEGER,
    total_corners INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_events_commence ON events(commence_time);
CREATE INDEX idx_events_status ON events(status);
CREATE INDEX idx_events_league ON events(league_key);

-- Snapshots de odds
CREATE TABLE odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    bookmaker_key TEXT NOT NULL,
    market_key TEXT NOT NULL,
    outcome TEXT NOT NULL,
    line REAL,
    price REAL NOT NULL,
    captured_at TIMESTAMP NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id)
);
CREATE INDEX idx_odds_event_market ON odds_snapshots(event_id, market_key);
CREATE INDEX idx_odds_captured ON odds_snapshots(captured_at);

-- Picks
CREATE TABLE picks (
    id TEXT PRIMARY KEY,                    -- UUID v7
    event_id TEXT NOT NULL,
    market_key TEXT NOT NULL,
    outcome TEXT NOT NULL,
    line REAL,
    reference_book TEXT NOT NULL,
    reference_price REAL NOT NULL,
    reference_prob REAL NOT NULL,
    devigging_method TEXT NOT NULL,
    comparison_book TEXT NOT NULL,
    comparison_price REAL NOT NULL,
    min_odds_for_value REAL NOT NULL,
    ev_at_comparison REAL NOT NULL,
    kelly_fraction REAL NOT NULL,
    stake_recommended_cop INTEGER NOT NULL,
    stake_pct_of_bankroll REAL NOT NULL,
    bankroll_at_generation_cop INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
        ('pending', 'placed', 'skipped', 'won', 'lost', 'pushed', 'void')),
    generated_at TIMESTAMP NOT NULL,
    generated_date DATE NOT NULL,           -- fecha (TZ del proyecto) calculada en Python al insertar, para idempotencia
    placed_at TIMESTAMP,
    actual_book TEXT,
    actual_price REAL,
    actual_stake_cop INTEGER,
    settled_at TIMESTAMP,
    pnl_cop INTEGER,
    clv REAL,
    closing_pinnacle_price REAL,
    skip_reason TEXT,
    notes TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id)
);
CREATE INDEX idx_picks_event ON picks(event_id);
CREATE INDEX idx_picks_status ON picks(status);
CREATE INDEX idx_picks_generated ON picks(generated_at);
CREATE UNIQUE INDEX idx_picks_unique ON picks(
    event_id, market_key, outcome,
    COALESCE(line, -999),
    generated_date
);

-- Ledger de bankroll: fuente de verdad
CREATE TABLE bankroll_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    book_code TEXT NOT NULL,                -- betplay | codere | rushbet | bwin
    movement_type TEXT NOT NULL CHECK(movement_type IN
        ('deposit', 'withdrawal', 'bet_stake', 'bet_payout', 'adjustment')),
    amount_cop INTEGER NOT NULL,            -- positivo = entra; negativo = sale
    related_pick_id TEXT,                   -- FK a picks (NULL para deposit/withdrawal/adjustment)
    notes TEXT,                             -- opcional, especialmente útil para adjustment
    FOREIGN KEY (related_pick_id) REFERENCES picks(id)
);
CREATE INDEX idx_bm_book ON bankroll_movements(book_code, occurred_at);
CREATE INDEX idx_bm_type ON bankroll_movements(movement_type);
CREATE INDEX idx_bm_pick ON bankroll_movements(related_pick_id);

-- Snapshot diario calculado por casa (cache, no fuente de verdad)
CREATE TABLE bankroll_book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date DATE NOT NULL,
    book_code TEXT NOT NULL,
    balance_cop INTEGER NOT NULL,
    deposits_today_cop INTEGER NOT NULL DEFAULT 0,
    withdrawals_today_cop INTEGER NOT NULL DEFAULT 0,
    stakes_today_cop INTEGER NOT NULL DEFAULT 0,
    payouts_today_cop INTEGER NOT NULL DEFAULT 0,
    picks_placed_today INTEGER NOT NULL DEFAULT 0,
    picks_won_today INTEGER NOT NULL DEFAULT 0,
    picks_lost_today INTEGER NOT NULL DEFAULT 0,
    pnl_today_cop INTEGER NOT NULL DEFAULT 0,
    UNIQUE(snapshot_date, book_code)
);
CREATE INDEX idx_bbs_date ON bankroll_book_snapshots(snapshot_date);

-- Estado global del sistema (singleton, id=1)
CREATE TABLE system_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    is_paused BOOLEAN NOT NULL DEFAULT 0,
    paused_reason TEXT,
    paused_at TIMESTAMP,
    last_pipeline_run_at TIMESTAMP,
    last_settlement_run_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO system_state (id, is_paused) VALUES (1, 0);

-- Tracking de cuota de APIs
CREATE TABLE api_quota_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,                 -- 'odds_api' | 'api_football'
    captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requests_remaining INTEGER,
    requests_used INTEGER,
    requests_limit INTEGER,
    endpoint TEXT,
    request_id TEXT
);
CREATE INDEX idx_aql_provider ON api_quota_log(provider, captured_at);

-- Log de operaciones
CREATE TABLE operation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_id TEXT,
    message TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX idx_oplog_occurred ON operation_log(occurred_at);
CREATE INDEX idx_oplog_level ON operation_log(level);
```

### Cálculo del bankroll vivo

```python
# Total bankroll por casa
SELECT book_code, SUM(amount_cop) AS balance_cop
FROM bankroll_movements
GROUP BY book_code;

# Bankroll total (suma de todas las casas)
SELECT SUM(amount_cop) AS total_balance_cop
FROM bankroll_movements;
```

`bankroll_book_snapshots` se recalcula al final de cada día (settlement de medianoche) a partir del ledger. Es solo un cache para queries rápidas de analytics, nunca fuente de verdad.

## 2. De-vigging: implementación exacta

### Multiplicativo (para mercados de 2-vías)

```python
def devig_multiplicative(prices: list[float]) -> list[float]:
    """
    Multiplicative de-vigging. Removes margin proportionally.
    Adecuado para mercados de 2 vías (totals, btts, spreads).
    """
    implied = [1 / p for p in prices]
    total = sum(implied)
    return [p / total for p in implied]
```

### Shin's method (para 1X2)

Implementación basada en Shin (1992, 1993) usando un root-finder robusto (`scipy.optimize.brentq`). El "Newton-Raphson manual" de implementaciones ingenuas puede divergir con favoritos extremos (Madrid 1.05 vs colero); `brentq` está garantizado a converger en el intervalo `[0, 0.5]` (z = insider trading proportion).

```python
from scipy.optimize import brentq

def devig_shin(prices: list[float], tol: float = 1e-10) -> tuple[list[float], float]:
    """
    Shin's de-vigging via brentq root-finding.
    Returns: (fair probabilities, z estimate).
    Raises: ValueError if solver does not converge or output does not satisfy invariants.

    Reference: H.S. Shin (1993), "Measuring the incidence of insider trading
    in a market for state-contingent claims", Economic Journal 103, pp. 1141-1153.
    """
    n = len(prices)
    if n < 2:
        raise ValueError("Shin requires at least 2 outcomes")

    pi = [1 / p for p in prices]
    B = sum(pi)

    # Caso sin overround (juego justo)
    if abs(B - 1.0) < tol:
        return pi, 0.0

    def fair_probs_given_z(z: float) -> list[float]:
        denominator = 2 * (1 - z)
        return [
            (((z ** 2) + 4 * (1 - z) * (p_i ** 2) / B) ** 0.5 - z) / denominator
            for p_i in pi
        ]

    def F(z: float) -> float:
        return sum(fair_probs_given_z(z)) - 1.0

    # brentq sobre [eps, 0.5 - eps] — z=0 es el caso sin insider, z=0.5 es el upper bound teórico
    eps = 1e-12
    try:
        z = brentq(F, eps, 0.5 - eps, xtol=tol, maxiter=200)
    except ValueError as e:
        raise ValueError(f"Shin solver did not bracket a root for prices={prices}") from e

    fair = fair_probs_given_z(z)

    # Invariantes: deben sumar 1 sin necesidad de re-normalizar
    s = sum(fair)
    if abs(s - 1.0) > 1e-8:
        raise ValueError(
            f"Shin convergence invariant violated: sum(fair)={s}, expected 1.0 "
            f"(prices={prices}, z={z})"
        )

    return fair, z
```

**Logging obligatorio**: cada llamada a `devig_shin` debe escribir una fila en `operation_log` con `operation='shin_devig'` y los inputs/outputs serializados en `metadata`. Esto es persistencia, no logger output — vive en DB y es independiente de `LOG_LEVEL`. El día que un pick parezca raro, podés reproducir el cálculo exacto query-eando la tabla.

```python
logger.debug(
    "shin_devig",
    prices=prices,
    z=z,
    fair_probs=fair,
    request_id=ctx.request_id,
)
```

**Tests obligatorios (TDD):**

```python
def test_devig_multiplicative_no_margin():
    result = devig_multiplicative([2.0, 2.0])
    assert abs(result[0] - 0.5) < 1e-10

def test_devig_multiplicative_with_margin():
    result = devig_multiplicative([1.91, 1.91])
    assert abs(sum(result) - 1.0) < 1e-10

def test_devig_shin_returns_zero_for_fair_market():
    fair, z = devig_shin([3.0, 3.0, 3.0])
    assert abs(z - 0.0) < 1e-8
    assert all(abs(p - 1/3) < 1e-8 for p in fair)

def test_devig_shin_favorite_unbiased():
    """Shin debe asignar menos prob al favorito que el multiplicativo
    (corrección por favorite-longshot bias)."""
    prices = [1.40, 4.50, 8.00]
    shin_probs, _ = devig_shin(prices)
    mult_probs = devig_multiplicative(prices)
    assert shin_probs[0] < mult_probs[0]

def test_devig_shin_known_case():
    prices = [2.10, 3.40, 3.60]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
    assert 0 < z < 0.1

def test_devig_shin_extreme_favorite():
    """Caso típico de partidos desbalanceados (LaLiga, Madrid vs colero)."""
    prices = [1.05, 12.0, 30.0]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
    assert fair[0] > 0.90
    assert all(p > 0 for p in fair)

def test_devig_shin_super_extreme():
    """Caso patológico para verificar robustez del solver."""
    prices = [1.02, 25.0, 80.0]
    fair, z = devig_shin(prices)
    assert abs(sum(fair) - 1.0) < 1e-8
```

## 3. Cálculo de EV y Kelly

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ValueAssessment:
    p_real: float
    odds: float
    ev: float
    min_odds_for_value: float
    kelly_fraction: float
    stake_recommended: float
    has_value: bool

def assess_value(
    p_real: float,
    odds: float,
    bankroll: float,
    min_ev: float = 0.025,
    kelly_divisor: float = 4.0,
    cap_pct: float = 0.03,
    floor_pct: float = 0.003,
) -> ValueAssessment:
    """
    Evalúa si una oportunidad tiene valor positivo y calcula stake según Kelly fraccional.
    Sin modos de staging: la decisión de cuánta plata real tener desplegada se controla
    fuera del código (depósitos en las casas vía Telegram).
    """
    ev = p_real * (odds - 1) - (1 - p_real)
    min_odds = 1 / p_real

    if ev < min_ev:
        return ValueAssessment(
            p_real=p_real, odds=odds, ev=ev,
            min_odds_for_value=min_odds,
            kelly_fraction=0.0,
            stake_recommended=0.0,
            has_value=False,
        )

    b = odds - 1
    q = 1 - p_real
    raw_kelly = (b * p_real - q) / b
    kelly_frac = raw_kelly / kelly_divisor

    if kelly_frac < floor_pct:
        stake_pct = 0.0
    else:
        stake_pct = min(kelly_frac, cap_pct)

    stake = round(bankroll * stake_pct, -3)

    return ValueAssessment(
        p_real=p_real, odds=odds, ev=ev,
        min_odds_for_value=min_odds,
        kelly_fraction=kelly_frac,
        stake_recommended=stake,
        has_value=True,
    )
```

## 4. Normalización de equipos

```python
from rapidfuzz import fuzz
from datetime import datetime
from dataclasses import dataclass

@dataclass
class MatchCandidate:
    odds_api_event: dict
    api_football_event: dict
    confidence: float

def match_events(
    odds_event: dict,
    candidates: list[dict],
    league_key: str,
    min_confidence: float = 90.0,
    time_window_hours: int = 6,
) -> MatchCandidate | None:
    odds_home = normalize_team_name(odds_event["home_team"])
    odds_away = normalize_team_name(odds_event["away_team"])
    odds_time = datetime.fromisoformat(odds_event["commence_time"])

    best: MatchCandidate | None = None

    for c in candidates:
        c_time = datetime.fromisoformat(c["commence_time"])
        if abs((odds_time - c_time).total_seconds()) > time_window_hours * 3600:
            continue

        c_home = normalize_team_name(c["home_team"])
        c_away = normalize_team_name(c["away_team"])

        score_home = fuzz.token_sort_ratio(odds_home, c_home)
        score_away = fuzz.token_sort_ratio(odds_away, c_away)
        confidence = (score_home + score_away) / 2

        if confidence >= min_confidence and (best is None or confidence > best.confidence):
            best = MatchCandidate(odds_event, c, confidence)

    return best


def normalize_team_name(name: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = ascii_name.lower().strip()
    for suffix in [' fc', ' cf', ' sc', ' ac', ' afc']:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)]
    return cleaned
```

## 5. Configs YAML

### `config/leagues.yaml`

```yaml
leagues:
  - key: soccer_epl
    api_football_id: 39
    name: Premier League
    country: England
    priority: high
    active: true

  - key: soccer_spain_la_liga
    api_football_id: 140
    name: LaLiga
    country: Spain
    priority: high
    active: true

  - key: soccer_italy_serie_a
    api_football_id: 135
    name: Serie A
    country: Italy
    priority: high
    active: true

  - key: soccer_germany_bundesliga
    api_football_id: 78
    name: Bundesliga
    country: Germany
    priority: high
    active: true

  - key: soccer_uefa_champs_league
    api_football_id: 2
    name: UEFA Champions League
    priority: high
    active: true

  - key: soccer_uefa_europa_league
    api_football_id: 3
    name: UEFA Europa League
    priority: medium
    active: true

  - key: soccer_fifa_world_cup
    api_football_id: 1
    name: FIFA World Cup 2026
    priority: high
    active: false                     # activar manualmente cuando empiece el torneo
```

### `config/markets.yaml`

```yaml
markets:
  - key: h2h
    name: 1X2
    outcomes: [home, draw, away]
    devigging_method: shin
    min_ev: 0.03
    enabled: true

  - key: totals
    name: Over/Under
    outcomes: [over, under]
    lines: [2.5]                    # lista — admite líneas alternativas en fase 1.5 (1.5, 3.5)
    devigging_method: multiplicative
    min_ev: 0.025
    enabled: true

  - key: btts
    name: Both Teams To Score
    outcomes: ["yes", "no"]              # quoted: YAML 1.1 parsea yes/no como bool sin comillas
    devigging_method: multiplicative
    min_ev: 0.025
    enabled: true

  - key: spreads
    name: Asian Handicap
    outcomes: [home, away]
    lines_strategy: sharp_consensus      # toma la línea del sharp reference, no se hardcodea
    devigging_method: multiplicative
    min_ev: 0.025
    enabled: true
```

### `config/books.yaml`

```yaml
sharp_reference:
  key: pinnacle
  region: eu
  description: "Market maker, used for p_real calculation"

comparison_books:
  - key: bet365
    region: eu
    enabled: true
  - key: betsson
    region: eu
    enabled: true
  - key: williamhill
    region: eu
    enabled: true
  - key: sport888
    region: eu
    enabled: true
  - key: betvictor
    region: eu
    enabled: true
  - key: marathonbet
    region: eu
    enabled: true

destination_books:
  - name: BetPlay
    code: betplay
    url: https://www.betplay.com.co
    allocation_pct: 30
    enabled: true
  - name: Codere
    code: codere
    url: https://codere.com.co
    allocation_pct: 25
    enabled: true
  - name: Rushbet
    code: rushbet
    url: https://www.rushbet.co
    allocation_pct: 25
    enabled: true
  - name: bwin
    code: bwin
    url: https://sports.bwin.co
    allocation_pct: 20
    enabled: true
```

### `config/bankroll.yaml`

```yaml
staking:
  method: kelly_fractional
  kelly_divisor: 4
  cap_pct: 0.03
  floor_pct: 0.003

risk_controls:
  pause_on_weekly_drawdown_pct: 0.05
  max_picks_per_day: 15
  max_stake_per_event_cop: 75000
```

(El bankroll inicial NO está aquí — se registra vía Telegram con `/deposit` por cada casa, ver CLAUDE.md.)

### `config/thresholds.yaml`

```yaml
data_freshness:
  max_odds_age_minutes: 30
  min_minutes_before_match: 60

matching:
  min_team_match_confidence: 90.0
  time_window_hours: 6

quality_gates:
  require_sharp_quoted: true           # exige cotización de Pinnacle para calcular p_real
  min_comparison_books_quoted: 2       # mínimo de casas EU con cuota antes de evaluar valor

notification:
  # Cuánto por encima de la cuota mínima sugerimos para stake "full" vs "half"
  full_stake_margin_pct: 0.04        # threshold_full = min_odds * (1 + 0.04)
  half_stake_margin_pct: 0.02        # threshold_half = min_odds * (1 + 0.02)

api_quota:
  alert_threshold_pct: 0.20          # alertar si quedan <20% de requests del plan

dead_mans_switch:
  ping_interval_minutes: 60
  alert_after_no_ping_hours: 8
```

## 6. Formato exacto de notificación Telegram

```
🎯 PICK detectado #234
━━━━━━━━━━━━━━━━━━━━━━
⚽ {home_team} vs {away_team}
🏆 {league_name} · {datetime_local} COL
📊 Mercado: {market_description}

📐 Prob. real (Pinnacle de-vigged): {p_real:.1%}
💰 Cuota mínima para EV+: {min_odds:.2f}
✨ Mejor cuota EU: {best_eu_book} @ {best_eu_price:.2f} → EV {ev:.1%}

🎲 Verificá tu casa (BetPlay/Codere/Rushbet/bwin):
   • Si conseguís ≥ {threshold_full:.2f} → stake {stake_full_cop:,} (Kelly/4)
   • Si conseguís ≥ {threshold_half:.2f} → stake {stake_half_cop:,} (Kelly reducido)
   • Si conseguís < {min_odds:.2f} → DESCARTAR

📝 Bankroll vivo: {bankroll_cop:,} COP
⏱️ Cuota expira aprox: {cutoff_local} ({min_minutes_before_match} min antes del partido)

[✅ Ya apostada] [🚫 Descartar] [🔍 Ver detalles]
```

Donde:
- `threshold_full` = `min_odds * (1 + thresholds.notification.full_stake_margin_pct)`.
- `threshold_half` = `min_odds * (1 + thresholds.notification.half_stake_margin_pct)`.
- `stake_full_cop` = `calculate_stake(bankroll, p_real, threshold_full, ...)`.
- `stake_half_cop` = `calculate_stake(bankroll, p_real, threshold_half, ...)` — **se recalcula Kelly a la cuota menor; no es `stake_full / 2`** (Kelly no es lineal en cuota).
- Si `threshold_half` resulta con `has_value=False` (EV bajo `min_ev`), se omite esa línea del mensaje.

## 7. Comportamiento de los botones Telegram

### "Ya apostada"
1. "¿En qué casa apostaste?" → inline buttons con las 4 casas.
2. "¿Qué cuota conseguiste?" → input texto, valida decimal.
3. "¿Stake real (COP)?" → input numérico.
4. Confirma, guarda `picks` con `status='placed'` y crea movimiento `bankroll_movements` (`movement_type='bet_stake'`, `amount_cop` negativo, `related_pick_id=<pick_id>`).

### "Descartar"
Pregunta motivo (4 botones predefinidos):
- "Cuota local insuficiente"
- "Sin saldo en casa"
- "No me convence"
- "Otro" → input texto

Guarda con `status='skipped'` y motivo en `skip_reason`. No genera movement.

### "Ver detalles"
- Historial de movimiento de cuotas últimas 6h.
- Cuotas en todas las casas EU disponibles.
- Si hay info de api-football: lesiones, ranking, h2h reciente.

## 8. Comandos de bankroll vía Telegram

| Comando | Descripción | Ejemplo |
|---|---|---|
| `/deposit <book> <amount>` | Registra depósito a una casa | `/deposit betplay 200000` |
| `/withdraw <book> <amount>` | Registra retiro de una casa | `/withdraw codere 100000` |
| `/adjust <book> <signed_amount> [razón]` | Ajuste manual (reconciliación) | `/adjust bwin -25000 bonus expirado` |
| `/balance` | Saldo total y per-casa | — |
| `/bankroll` | Resumen: saldo + picks pendientes + P&L día/semana/mes | — |

Cada comando:
1. Valida que el `book_code` esté en `config/books.yaml`.
2. Crea fila en `bankroll_movements` con el `movement_type` correspondiente y `notes` opcional.
3. Responde con el nuevo balance de esa casa y el bankroll total.

`/deposit` y `/withdraw` toman `amount > 0`; el signo se aplica internamente según `movement_type`. `/adjust` toma `amount` con signo explícito (positivo o negativo).

### Otros comandos Telegram

| Comando | Descripción |
|---|---|
| `/start` | Inicializa el bot |
| `/status` | Estado del sistema (corridas, pausa, quota APIs) |
| `/today` | Picks de hoy |
| `/week` | Resumen semanal |
| `/pending` | Picks pendientes de confirmar |
| `/pause [razón]` | Pausa manual del pipeline |
| `/resume` | Reanudar pipeline (limpia `system_state.is_paused`) |
| `/help` | Ayuda |

## 9. Estructura Google Sheets

### Hoja "Picks"
| Fecha | Hora | Liga | Partido | Mercado | Outcome | Cuota Min | Cuota EU Ref | EV % | Stake Sugerido | Estado | Cuota Real | Casa Real | Stake Real | Resultado | P&L | CLV | Notas |

### Hoja "Bankroll"
| Fecha | Saldo BetPlay | Saldo Codere | Saldo Rushbet | Saldo bwin | Total | Deposits Día | Withdrawals Día | Stakes Día | Payouts Día | Picks Hoy | Ganados | Perdidos | P&L Día | Drawdown vs Peak |

### Hoja "Movements"
| Timestamp | Casa | Tipo | Monto | Pick ID | Source | Notas |

Replica `bankroll_movements` para auditoría manual fuera del sistema.

### Hoja "Métricas"
Dashboards con fórmulas agrupados por liga, mercado, rango de cuotas, casa, mes.

### Hoja "Sesgo Casas"
| Casa Local | Casa EU | Mercado | Rango Cuota EU | N Observaciones | Ratio Promedio | Std Dev |

## 10. Scheduling con systemd

```ini
# betting-bot-pipeline.timer
[Unit]
Description=Run betting bot pipeline on schedule

[Timer]
OnCalendar=*-*-* 05:00:00 America/Bogota
OnCalendar=*-*-* 09:00:00 America/Bogota
OnCalendar=*-*-* 13:00:00 America/Bogota
OnCalendar=*-*-* 17:00:00 America/Bogota
OnCalendar=*-*-* 20:00:00 America/Bogota
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# betting-bot-settle.timer
[Unit]
Description=Settle finished matches

[Timer]
OnCalendar=*:0/30
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# betting-bot-heartbeat.timer
[Unit]
Description=Dead-man's switch ping to healthchecks.io

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# betting-bot-heartbeat.service
[Unit]
Description=Ping healthchecks.io to signal the bot is alive
After=network.target

[Service]
Type=oneshot
User=betting_bot
WorkingDirectory=/home/betting_bot/betting_bot
ExecStart=/home/betting_bot/.local/bin/uv run python -m betting_bot.cli.heartbeat
```

Cada `.timer` necesita un `.service` con el mismo nombre. Los otros dos jobs batch necesitan también el suyo:

```ini
# betting-bot-pipeline.service
[Unit]
Description=Run betting bot pipeline
After=network.target

[Service]
Type=oneshot
User=betting_bot
WorkingDirectory=/home/betting_bot/betting_bot
ExecStart=/home/betting_bot/.local/bin/uv run python -m betting_bot.cli.run_pipeline
```

```ini
# betting-bot-settle.service
[Unit]
Description=Settle finished matches
After=network.target

[Service]
Type=oneshot
User=betting_bot
WorkingDirectory=/home/betting_bot/betting_bot
ExecStart=/home/betting_bot/.local/bin/uv run python -m betting_bot.cli.settle
```

```ini
# betting-bot-telegram.service
[Unit]
Description=Betting bot Telegram listener (long-running)
After=network.target

[Service]
Type=simple
User=betting_bot
WorkingDirectory=/home/betting_bot/betting_bot
ExecStart=/home/betting_bot/.local/bin/uv run python -m betting_bot.cli.telegram_listener
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
```

El listener de Telegram corre como servicio persistente (long-polling). Los timers disparan los jobs batch (pipeline, settle, heartbeat).

Por qué systemd timers en vez de cron:
- Mejor logging (journalctl).
- `Persistent=true` recupera ejecuciones perdidas tras reboot.
- Dependencias explícitas entre servicios.
- Sin conflictos de PATH.

## 11. Dead-man's switch

Cada corrida exitosa del pipeline (al final del proceso, sin errores) hace:

```python
httpx.get(settings.healthchecks_url, timeout=10)
```

Además, `betting-bot-heartbeat.timer` pinguea cada hora aunque no haya corrida. healthchecks.io tiene configurada una grace period de 8h: si no llega ping en ese tiempo, dispara email + integración con Telegram.

URL única generada al crear el check en https://healthchecks.io/ y guardada en `.env` como `HEALTHCHECKS_URL`.

## 12. Roadmap de fases

### Fase 1: MVP (4 semanas)
- Pipeline E2E con 4 mercados.
- Notificaciones Telegram + bot interactivo (deposits/withdrawals).
- Tracking Sheets desde día 6 (Sheets antes de E2E para trazabilidad).
- Settlement automático.
- Ledger de bankroll con movimientos vía Telegram.
- Schemas Pydantic de respuestas API como contrato testeable.
- Fixtures JSON reales sanitizados en `tests/fixtures/`.
- Tests >80% coverage en módulos críticos.
- Validación del sistema con bankroll de juguete antes del Mundial; transición a prod con DB limpia y deposits reales.

### Fase 1.5 (gap entre MVP y operación masiva)
- Líneas alternativas en totals/spreads (1.5, 3.5).
- Política de retención de `odds_snapshots` (90 días + agregados).
- Refinamiento de UI Telegram según uso real.

### Fase 2: Refinamiento (4–8 semanas post-Mundial)
- Análisis de sesgo casas COL.
- Pre-filtrado automático basado en aprendizaje.
- Mercados córners y tarjetas.
- Dashboard web local.
- Posible integración LLM para anotaciones cualitativas.

### Fase 3: Edge propio (TBD)
- Modelo Dixon-Coles + xG.
- Backtest sobre histórico acumulado.
- Comparación modelo propio vs Pinnacle.
- Optimización de thresholds basada en data real.
- Manejo de Kelly con correlación entre picks del mismo evento.
