# Betting Bot — Runbook & Implementation Plan

> Guía operativa y plan de implementación. Complementa CLAUDE.md (contexto general) y DESIGN.md (especificaciones técnicas).

## Tabla de contenidos
1. Cuentas y APIs: orden de creación
2. Setup local de desarrollo
3. Etapas de implementación (1–7): MVP funcional
4. Etapas de implementación (8–10): robustez y operación real
5. Deployment al servidor Vultr
6. Operación diaria
7. Troubleshooting común
8. Backup y checklists de seguridad
9. Monitoreo externo (healthchecks.io)

---

## 1. Cuentas y APIs: orden de creación

Crear estas cuentas/recursos en este orden, **en paralelo** con setup local.

### 1.1 the-odds-api (CRÍTICO, hacer primero)

1. https://the-odds-api.com/ → Sign up.
2. Confirmar email.
3. Plan **gratis** para probar. Subir a **20K ($30/mes)** apenas integrado.
4. Copiar API key al password manager.
5. Activar billing.

**Test:**
```bash
curl "https://api.the-odds-api.com/v4/sports/?apiKey=YOUR_KEY"
```

### 1.2 api-football (api-sports.io)

1. https://www.api-football.com/ → Sign up.
2. Plan gratis (100 req/día) para validar.
3. Subir a **Pro ($19/mes, 7500 req/día)** al integrar.
4. Copiar API key.

**Test (suscripción directa en api-sports.io):**
```bash
curl -H "x-apisports-key: YOUR_KEY" "https://v3.football.api-sports.io/status"
```

> El header es `x-apisports-key` cuando te suscribís directo en api-sports.io. Si en algún momento usás la versión de RapidAPI Marketplace, ahí cambia a `x-rapidapi-key` + `x-rapidapi-host`. Para este proyecto, **suscripción directa**.

### 1.3 Telegram Bot

1. Buscar `@BotFather` en Telegram.
2. `/newbot` → asignar nombre y username (debe terminar en `bot`).
3. Guardar TOKEN en el password manager.
4. Configurar comandos con `/setcommands`:
   ```
   start - Inicializar bot
   status - Estado del sistema
   today - Picks de hoy
   week - Resumen semanal
   pending - Picks pendientes de confirmar
   balance - Saldo por casa
   bankroll - Resumen completo de bankroll
   deposit - Registrar depósito (deposit <casa> <monto>)
   withdraw - Registrar retiro (withdraw <casa> <monto>)
   adjust - Ajuste manual (adjust <casa> <monto> [razón])
   pause - Pausar pipeline
   resume - Reanudar pipeline
   help - Ayuda
   ```
5. Obtener CHAT_ID:
   - Mandar `/start` al bot.
   - Visitar `https://api.telegram.org/botYOUR_TOKEN/getUpdates`.
   - Buscar `"chat":{"id":NUMBER}`.

### 1.4 Google Cloud Service Account para Sheets

1. https://console.cloud.google.com/ → crear proyecto "betting-bot".
2. APIs & Services → Library → Enable "Google Sheets API".
3. IAM & Admin → Service Accounts → Create Service Account.
   - Nombre: `betting-bot-sheets`.
   - Skip roles.
4. Service Account creado → Keys → Add key → Create new key → JSON.
   - Descarga archivo `.json` y guardalo en `credentials/sheets-sa.json` del repo (la carpeta ya está en `.gitignore`).
5. Crear Google Sheet (URL → copiar SHEET_ID entre `/d/` y `/edit`).
6. Compartir el sheet con el `client_email` del JSON con permisos Editor.
7. Crear 5 hojas vacías en el sheet: "Picks", "Bankroll", "Movements", "Métricas", "Sesgo Casas".

### 1.5 healthchecks.io (dead-man's switch)

1. https://healthchecks.io/ → Sign up (free tier: 20 checks, suficiente).
2. Crear check "betting-bot-pipeline".
   - Schedule: Period **1 hour**, Grace time **8 hours**.
3. Configurar integraciones (sección **Integrations** del menú principal, a nivel de cuenta):
   - **Email**: queda configurado automáticamente con tu cuenta.
   - **Telegram** (opcional, recomendado): seguir el flow para vincular `@healthchecks_io_bot` a tu Telegram. Es un bot distinto al del proyecto.
   - Asignar ambas integraciones al check.
4. Copiar la URL del ping (formato `https://hc-ping.com/<uuid>`) → la guardás para pegar en `HEALTHCHECKS_URL` cuando armes el `.env` en Etapa 1.
5. **Pausar el check** (botón "Pause" en la página del check) mientras desarrollás. Mientras no haya sistema corriendo, no querés alertas falsas cada 8h. Lo reanudás en Etapa 10 al deployar.

### 1.6 Cuentas en casas de apuestas (paralelo)

- BetPlay: https://www.betplay.com.co
- Codere: https://codere.com.co
- Rushbet: https://www.rushbet.co
- bwin: https://sports.bwin.co

**Completar KYC en cada una antes de la primera apuesta.**

---

## 2. Setup local de desarrollo

### 2.1 Prerrequisitos

- Python 3.12+ (uv lo maneja).
- git.
- Editor (Cursor / VS Code).
- Entorno: WSL Ubuntu 20.04 (sigue las convenciones del usuario; comandos en `wsl bash -c '...'`).

### 2.2 Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

### 2.3 Sincronizar dependencias

El repo ya viene inicializado (`pyproject.toml`, estructura de carpetas, configs). Solo hace falta crear el venv y bajar deps:

```bash
cd ~/Proyectos/Personales/betting_bot
uv sync
```

Esto crea `.venv/`, resuelve y bloquea versiones en `uv.lock`, e instala todas las deps declaradas en `pyproject.toml` (core y dev).

Justificación de `scipy`: lo necesitamos para `scipy.optimize.brentq` (root-finder de Shin). Es ~80MB pero es la única alternativa robusta a un Newton-Raphson manual frágil.

### 2.5 Variables de entorno

```bash
# .env.example
API_FOOTBALL_KEY=
ODDS_API_KEY=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Sheets
GOOGLE_SERVICE_ACCOUNT_JSON_PATH=./credentials/sheets-sa.json
GOOGLE_SHEET_ID=

# DB
DATABASE_URL=sqlite:///data/betting_bot.db

# Dead-man's switch
HEALTHCHECKS_URL=

# Operación
LOG_LEVEL=INFO
TIMEZONE=America/Bogota
```

> Sobre toggles tipo `ENABLE_WEEKLY_REPORT` / `ENABLE_REFRESH_RUNS`: no existen en fase 1. Activar/desactivar runs se hace habilitando o deshabilitando los `systemd timers` correspondientes, no con variables de entorno.

**NO existe `BANKROLL_INITIAL_COP` ni `STAGING_MODE` ni `PAPER_TRADING`.** El bankroll se setea con `/deposit` por Telegram. La distinción entre "ambiente de pruebas" y "ambiente real" se hace fuera del código (qué valores pongas en `.env`, qué DB cargues).

```bash
# .gitignore
.env
.env.*
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
data/
credentials/
*.db
*.db-journal
*.db-wal
*.db-shm
backups/
.coverage
htmlcov/
.DS_Store
.idea/
.vscode/
```

### 2.6 CHANGELOG.md inicial

Crear `CHANGELOG.md` en la raíz desde la primera etapa:

```markdown
# Changelog

Todos los cambios significativos del proyecto se registran aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/).

## [Sin publicar]

### Hecho
- (siguiente sesión)

### Pendientes
- (siguiente sesión)
```

---

## 3. Etapas de implementación (1–7): MVP funcional

Cada etapa es un bloque de trabajo coherente con entregable verificable. No hay estimaciones de tiempo: cada etapa tarda lo que tenga que tardar. La progresión es secuencial — cada etapa asume completada la anterior.

### Etapa 1: Setup y primer health-check

**Objetivo:** todo conectado, sin lógica de negocio aún.

- [ ] Crear cuentas externas (the-odds-api, api-football, bot Telegram, service account Google + sheet, healthchecks.io).
- [ ] `cp .env.example .env` y llenar valores.
- [ ] `uv sync` para instalar dependencias y crear venv.
- [ ] Implementar `config.py` con Pydantic Settings.
- [ ] Implementar `cli/healthcheck.py`: ping a las 3 APIs + Telegram + Sheets + healthchecks.io.
- [ ] Capturar fixtures JSON reales (sanitizados) de ambas APIs → `tests/fixtures/`.
- [ ] Probar `uv run python -m betting_bot.cli.healthcheck`.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** comando que reporta "✅ Todas las APIs conectadas" o falla informativamente. Fixtures reales capturados.

### Etapa 2: DB y modelos

**Objetivo:** persistencia funcionando, migraciones aplicadas.

- [ ] Implementar `persistence/models.py` con SQLAlchemy según schema de DESIGN.md sección 1.
- [ ] Setup Alembic: `uv run alembic init src/betting_bot/persistence/migrations`.
- [ ] Primera migración: `uv run alembic revision --autogenerate -m "initial schema"`.
- [ ] Aplicar: `uv run alembic upgrade head`.
- [ ] Implementar `persistence/repo.py` con EventRepo, OddsRepo, PickRepo.
- [ ] Implementar `bankroll/ledger.py`: API sobre `bankroll_movements` (record_deposit, record_withdrawal, record_bet_stake, record_bet_payout, record_adjustment, get_balance_by_book, get_total_balance).
- [ ] **Tests unitarios de repos y ledger con SQLite en memoria (TDD).** Coverage del ledger >90% (es lógica financiera).
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** `data/betting_bot.db` creado con tablas correctas, tests passing.

### Etapa 3: Ingesta con contratos

**Objetivo:** traer fixtures y odds reales con schemas Pydantic.

- [ ] **Escribir tests con fixtures JSON capturados en Etapa 1 PRIMERO (TDD).**
- [ ] Implementar `ingestion/schemas.py`: Pydantic models de respuestas the-odds-api y api-football.
- [ ] Implementar `ingestion/fixtures.py`: cliente api-football.
- [ ] Implementar `ingestion/odds.py`: cliente the-odds-api. Registrar quota en `api_quota_log` después de cada request (parseando `x-requests-remaining`).
- [ ] Implementar `ingestion/normalizer.py`: matching con rapidfuzz.
- [ ] CLI: `uv run python -m betting_bot.cli.run_pipeline --ingest-only`.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** tablas `events`, `odds_snapshots` y `api_quota_log` con data real.

### Etapa 4: Pricing

**Objetivo:** de-vigging y cálculo EV con tests exhaustivos.

- [ ] **Tests obligatorios PRIMERO (TDD) según DESIGN.md sección 2.** Incluir tests de casos extremos: `[1.05, 12.0, 30.0]` y `[1.02, 25.0, 80.0]`.
- [ ] Implementar `pricing/devigging.py`: multiplicativo + Shin con `scipy.optimize.brentq`. El `assert sum(fair) == 1` es parte del contrato, no se re-normaliza.
- [ ] Implementar `pricing/value.py`: `assess_value()` sin modos de staging.
- [ ] Implementar `pricing/kelly.py`: Kelly/4 con cap y floor.
- [ ] Coverage en `pricing/` > 95%.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** dado un evento con odds, generar lista de picks con todos los campos.

### Etapa 5: Telegram bot — comandos básicos y bankroll

**Objetivo:** bot interactivo recibiendo deposits/withdrawals y mostrando estado.

- [ ] Implementar `delivery/telegram_bot.py` con python-telegram-bot.
- [ ] Implementar `delivery/telegram_handlers.py`:
  - `/start`, `/help`, `/status`, `/balance`, `/bankroll`.
  - `/deposit`, `/withdraw`, `/adjust` (escriben a `bankroll_movements`).
  - `/pause`, `/resume` (escriben a `system_state`).
- [ ] Validar parsing de comandos (montos, casas).
- [ ] Test manual: registrar deposits de juguete, verificar `/balance` y `/bankroll`.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** bot funcionando; podés simular tu bankroll inicial con comandos.

### Etapa 6: Sheets sync — trazabilidad desde el primer pick

**Objetivo:** Sheets funcionando antes del pipeline E2E para tener trazabilidad inmediata.

- [ ] Implementar `delivery/sheets_sync.py` con gspread.
- [ ] `sync_pick_to_sheets(pick)` → hoja "Picks".
- [ ] `sync_movement_to_sheets(movement)` → hoja "Movements".
- [ ] `sync_bankroll_snapshot()` → hoja "Bankroll".
- [ ] Notificaciones de pick (formato DESIGN sección 6) + handlers de botones inline (wizard "Ya apostada" / "Descartar").
- [ ] Test manual: generar pick fake, mandar a Telegram, confirmar wizard escribe a `picks`, `bankroll_movements` y Sheets.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** pick fake llegando a Telegram, wizard funcional, todo reflejado en Sheets.

### Etapa 7: Pipeline E2E + revisión

**Objetivo:** un comando que hace todo end-to-end con trazabilidad completa.

- [ ] `cli/run_pipeline.py` orquesta: ingestion → matching → pricing → persist → notify → sheets sync.
- [ ] Filtro por `--league <key>`.
- [ ] Pipeline checkea `system_state.is_paused` al inicio; si true, log + exit.
- [ ] Pipeline pingea `HEALTHCHECKS_URL` al final si todo OK.
- [ ] Logging estructurado con `request_id` propagado.
- [ ] Manejo de errores: si una API falla 1-2 veces, log + skip. A las 3 fallas, alerta a Telegram + pausa.
- [ ] **Tests de integración (con fixtures de Etapas 1 y 3).**
- [ ] Revisar logs acumulados, identificar bugs.
- [ ] **Actualizar CHANGELOG.md.**

**Entregable:** `uv run python -m betting_bot.cli.run_pipeline` corre completo. Lista de mejoras para las siguientes etapas.

---

## 4. Etapas de implementación (8–10): robustez y operación real

### Etapa 8: Settlement, comandos avanzados, robustez

- Implementar `settlement/settle.py`: poll de resultados, P&L, escribir `bet_payout` en ledger.
- Comandos Telegram avanzados: `/today`, `/week`, `/pending`.
- Job para `bankroll/snapshots.py`: recalcula `bankroll_book_snapshots` al final del día.
- Métricas básicas en hoja "Métricas".
- Reconciliation script (`cli/reconcile.py`): verifica que sumas del ledger cuadren con lo esperado.
- Manejo de errores robusto: retries con backoff, alertas a Telegram si pipeline falla.
- Auto-pausa por drawdown: detecta caída >5% semanal y setea `system_state.is_paused=true`.

### Etapa 9: Refresh runs, sesgo casas, edge cases

- Validar refresh runs (4 corridas/día) en ambiente local.
- Hoja "Sesgo Casas" se actualiza con cada settle.
- Manejo de timezones para Mundial (kickoffs en horarios raros).
- Tests de integración E2E completos.
- Stress test: simular día con 50+ partidos.
- Healthcheck de quota APIs en tiempo real (alerta si <20%).

### Etapa 10: Hardening y transición a operación real

- Deploy al servidor Vultr (sección 5).
- Configurar systemd timers.
- Monitoring: log rotation, backup automático.
- **Reanudar el check de healthchecks.io** (botón "Resume" en la página del check). Verificar que el primer ping del heartbeat lo saca de estado "paused" a "up".
- **Validación previa** con bankroll de juguete corriendo en el servidor:
  - CLV cercano a 0 o positivo.
  - Sin convergencia fallida en Shin.
  - Reconciliation sin inconsistencias.
- **Transición a operación real**:
  - `rm data/betting_bot.db` en el servidor.
  - `uv run alembic upgrade head` (DB limpia).
  - Registrar deposits reales vía Telegram con los montos efectivamente depositados en cada casa.
  - Primer día de operación real.

---

## 5. Deployment al servidor Vultr

**Sin Docker.** Python en venv + systemd. Menos capas para debug.

### 5.1 Acceso inicial

```bash
ssh root@TU_IP_VULTR

# Crear usuario non-root
adduser betting_bot
usermod -aG sudo betting_bot
mkdir -p /home/betting_bot/.ssh
cp ~/.ssh/authorized_keys /home/betting_bot/.ssh/
chown -R betting_bot:betting_bot /home/betting_bot/.ssh
chmod 700 /home/betting_bot/.ssh
chmod 600 /home/betting_bot/.ssh/authorized_keys

exit
ssh betting_bot@TU_IP_VULTR
```

### 5.2 Hardening básico

```bash
sudo apt update && sudo apt upgrade -y
sudo ufw allow OpenSSH
sudo ufw enable

sudo apt install -y fail2ban sqlite3 python3.12 python3.12-venv
sudo systemctl enable fail2ban

# Disable root SSH
sudo sed -i 's/^PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
```

### 5.3 Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv --version
```

### 5.4 Deploy del código

```bash
cd /home/betting_bot
git clone <repo-url> betting_bot
cd betting_bot

cp .env.example .env
nano .env                # rellenar con los valores reales

mkdir -p data credentials logs backups

# Copiar service account JSON desde local
# (desde tu WSL):
#   scp credentials/sheets-sa.json betting_bot@TU_IP:/home/betting_bot/betting_bot/credentials/

uv sync
uv run alembic upgrade head
uv run python -m betting_bot.cli.healthcheck
```

### 5.5 Systemd units

```bash
sudo cp ops/systemd/*.service /etc/systemd/system/
sudo cp ops/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Servicios long-running
sudo systemctl enable --now betting-bot-telegram.service

# Timers batch
sudo systemctl enable --now betting-bot-pipeline.timer
sudo systemctl enable --now betting-bot-settle.timer
sudo systemctl enable --now betting-bot-heartbeat.timer

systemctl list-timers --all | grep betting-bot
systemctl status betting-bot-telegram.service
```

### 5.6 Setup inicial del bankroll en prod

Desde Telegram, con la plata ya depositada en cada casa:

```
/deposit betplay 750000
/deposit codere 625000
/deposit rushbet 625000
/deposit bwin 500000
/balance
```

Verificar que `/balance` muestra 2,500,000 total (en la moneda del deployment).

---

## 6. Operación diaria

### Mañana (después de corrida 5 AM)
1. Revisar picks en Telegram.
2. Para cada pick:
   - Verificar cuota actual en casa colombiana.
   - Si ≥ cuota mínima → apostar.
   - Botón "Ya apostada" → registrar casa/cuota/stake (el bot escribe `bet_stake` en ledger).
   - Si no aguanta → "Descartar" con motivo.

### Durante el día
- Updates en refresh runs (9, 13, 17, 20 COL).
- Nuevos picks → mismo flujo.
- Recargas/retiros de casas → `/deposit <casa> <monto>` o `/withdraw <casa> <monto>`.

### Noche
- Settlements automáticos cada 30 min (escriben `bet_payout` en ledger).
- Si hay picks sin settle a las 23:59, bot alerta.
- Revisar Sheets para P&L del día.

### Domingo
- Reporte semanal automático a las 22:00.

### Pausa por drawdown
- Si bankroll cae >5% en la semana, sistema setea `system_state.is_paused=true`.
- Bot manda mensaje. Revisar antes: ¿mala suerte o bug?
- Confirmar con `/resume`.

---

## 7. Troubleshooting común

### "El bot no manda nada"
1. `journalctl -u betting-bot-pipeline.service --since "2 hours ago"`.
2. `systemctl status betting-bot-pipeline.timer`.
3. `systemctl status betting-bot-telegram.service`.
4. Verificar partidos próximos en ligas activas.
5. Verificar quota the-odds-api: `sqlite3 data/betting_bot.db "SELECT * FROM api_quota_log ORDER BY id DESC LIMIT 5"`.
6. Verificar pausa: `sqlite3 data/betting_bot.db "SELECT * FROM system_state"`.

### "Picks duplicados"
- Bug en idempotencia. Revisar UNIQUE INDEX en `picks` (debe usar `generated_date` calculado en Python, no `date(generated_at)`).

### "Matching erróneo de equipo"
- Revisar `operation_log` para warnings.
- Agregar excepción manual en `normalizer.py`.

### "Rate limit excedido"
- Query `api_quota_log` para historial.
- Reducir `comparison_books`, upgrade plan, o más caching.

### "Sheets no se actualiza"
- Service account tiene permisos Editor en el sheet correcto.
- `GOOGLE_SHEET_ID` correcto (verificá que apunta al sheet esperado).

### "Telegram no recibe mensajes"
- TOKEN y CHAT_ID correctos.
- Iniciaste conversación con el bot (`/start`).
- `systemctl status betting-bot-telegram.service`.

### "Bankroll desincronizado"
- `uv run python -m betting_bot.cli.reconcile`.
- Si hay discrepancia con saldo real en la casa: `/adjust <casa> <delta> razón`.

### "healthchecks.io me alertó pero el bot está OK"
- Revisar si el último ping llegó: `journalctl -u betting-bot-heartbeat.service --since "1 day ago"`.
- Verificar conectividad: `curl $HEALTHCHECKS_URL`.

### "Shin no converge en algún partido"
- El `assert` en `devig_shin` debe haber tirado `ValueError`. Buscar en `operation_log`.
- Loggear los `prices` del caso y agregar un test de regresión.

---

## 8. Backup y checklists de seguridad

### Antes del primer dinero real apostado
- [ ] Tests passing con coverage >80% en `pricing/`, `bankroll/`.
- [ ] Validación en dev 1 semana limpio.
- [ ] De-vigging validado contra calculadora externa (al menos 5 casos).
- [ ] CLV del dev cercano a 0 o positivo.
- [ ] Reconciliation sin inconsistencias.
- [ ] Backup automático del DB configurado y verificado.
- [ ] Acceso al servidor solo por SSH key (PasswordAuthentication=no).
- [ ] healthchecks.io vinculado y testeado (forzá una pausa de >8h en dev y verificá que dispare la alerta).
- [ ] DB limpia (`rm data/betting_bot.db && uv run alembic upgrade head`).
- [ ] Deposits reales registrados vía Telegram (saldos coinciden con los efectivamente depositados en cada casa).

### Backup automático — SAFE con WAL

Usar `sqlite3 ".backup"`, **nunca `cp` directo** (con WAL activo, `cp` puede dar archivo corrupto).

```bash
# /home/betting_bot/backup.sh
#!/bin/bash
set -euo pipefail
DATE=$(date +%Y%m%d_%H%M%S)
DB=/home/betting_bot/betting_bot/data/betting_bot.db
DEST_DIR=/home/betting_bot/backups
DEST=$DEST_DIR/betting_bot_$DATE.db

mkdir -p "$DEST_DIR"

# Online backup safe con WAL
sqlite3 "$DB" ".backup '$DEST'"

# Verificar integridad
sqlite3 "$DEST" "PRAGMA integrity_check" | grep -q "^ok$" || {
    echo "BACKUP CORRUPTO: $DEST" >&2
    exit 1
}

gzip "$DEST"
find "$DEST_DIR" -name "*.gz" -mtime +30 -delete
```

Crontab:
```
0 3 * * * /home/betting_bot/backup.sh >> /home/betting_bot/logs/backup.log 2>&1
```

---

## 9. Monitoreo externo (healthchecks.io)

El sistema tiene **tres líneas de defensa** para detectar fallas:

1. **Internal alerts**: si una API falla 3 veces consecutivas, alerta a Telegram (lógica dentro del pipeline).
2. **Drawdown auto-pause**: si bankroll cae >5% semanal, sistema se pausa solo y avisa.
3. **External dead-man's switch (healthchecks.io)**: cubre el caso "el sistema ni siquiera está corriendo" (Vultr cayó, systemd desactivado, lock colgado).

### Cómo funciona

- `betting-bot-heartbeat.timer` corre cada hora y hace `curl $HEALTHCHECKS_URL`.
- El pipeline también hace ping al final de cada corrida exitosa.
- Si healthchecks.io no recibe ping en 8h, dispara email + mensaje al bot.

### Test del dead-man's switch

Una vez configurado, **forzar una falla en dev** para verificar que la alerta llega:

```bash
sudo systemctl stop betting-bot-heartbeat.timer
# esperar 8h o ajustar grace period temporalmente a 1h
```

Si no llega la alerta, la integración está mal y no detectarás caídas en prod.

### Free tier de healthchecks.io

20 checks, integraciones email + Slack + Discord + Telegram (vía bot custom o aplicación oficial). Suficiente para este proyecto.
