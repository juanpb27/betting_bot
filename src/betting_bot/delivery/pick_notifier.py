"""Construcción del mensaje + teclado inline de notificación de un pick.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from betting_bot.delivery.telegram_handlers import escape_md, fmt_amount
from betting_bot.persistence.models import Event, Pick
from betting_bot.pricing.value import assess_value
from betting_bot.yaml_config import NotificationConfig, StakingConfig

# Prefijo corto para que el callback_data quepa en 64 bytes con UUID v4
# (36 chars). Formato: `pa:<action>:<pick_id>` donde <action> ∈ {placed,
# skip, details} — máximo 7 chars. Total ≤ 4 + 7 + 1 + 36 = 48 bytes.
_CB_PREFIX = "pa"
_VALID_ACTIONS: frozenset[str] = frozenset({"placed", "skip", "details"})


def parse_pick_action(callback_data: str) -> tuple[str, str]:
    """Parsea `pa:<action>:<pick_id>` → `(action, pick_id)`.

    Helper para que el wizard de Etapa 6 dispatchee callbacks sin re-inventar
    el formato. Sin esto, el wizard inline el `split` y el formato queda como
    contrato implícito entre dos módulos — el día que se cambie, rompe en
    runtime sin que mypy avise.
    """
    parts = callback_data.split(":", 2)
    if len(parts) != 3 or parts[0] != _CB_PREFIX:
        raise ValueError(
            f"callback_data con formato inesperado: {callback_data!r} "
            f"(esperado '{_CB_PREFIX}:<action>:<pick_id>')"
        )
    _, action, pick_id = parts
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"acción desconocida: {action!r}. Esperada una de: {sorted(_VALID_ACTIONS)}"
        )
    if not pick_id:
        raise ValueError("pick_id vacío en callback_data")
    return action, pick_id


def threshold_for_margin(*, min_odds: float, margin_pct: float) -> float:
    """`min_odds * (1 + margin_pct)`. Redondeo a 2 decimales para evitar
    ruido FP en la presentación (`1.99 * 1.04 = 2.0696` → `2.07`)."""
    return round(min_odds * (1 + margin_pct), 2)


def _fmt_odds(price: float) -> str:
    """Cuotas en MarkdownV2 van dentro de backticks para evitar escapar `.`.
    Devuelve el string ya delimitado: `` `1.86` ``."""
    return f"`{price:.2f}`"


def build_pick_message(
    *,
    pick: Pick,
    event: Event,
    notification: NotificationConfig,
    staking: StakingConfig,
    min_ev: float,
) -> str:
    """Texto MarkdownV2 de la notificación. Cada componente variable va
    escapado o dentro de spans `code` para evitar BadRequest de Telegram."""
    full_th = threshold_for_margin(
        min_odds=pick.min_odds_for_value, margin_pct=notification.full_stake_margin_pct
    )
    half_th = threshold_for_margin(
        min_odds=pick.min_odds_for_value, margin_pct=notification.half_stake_margin_pct
    )

    # Recálculo de Kelly a la cuota menor (es la cuota que CONSEGUIRÍA el user
    # en su casa local). Si no hay valor a esa cuota, la línea se omite.
    full_assess = assess_value(
        pick.reference_prob,
        full_th,
        bankroll=pick.bankroll_at_generation,
        min_ev=min_ev,
        kelly_divisor=staking.kelly_divisor,
        cap_pct=staking.cap_pct,
        floor_pct=staking.floor_pct,
        rounding_unit=staking.stake_rounding_unit,
    )
    half_assess = assess_value(
        pick.reference_prob,
        half_th,
        bankroll=pick.bankroll_at_generation,
        min_ev=min_ev,
        kelly_divisor=staking.kelly_divisor,
        cap_pct=staking.cap_pct,
        floor_pct=staking.floor_pct,
        rounding_unit=staking.stake_rounding_unit,
    )

    lines: list[str] = []
    lines.append("*PICK detectado*")
    lines.append(
        f"*{escape_md(event.home_team)} vs {escape_md(event.away_team)}*"
    )
    lines.append(
        f"{escape_md(event.league_key)} · {escape_md(pick.market_key)} · "
        f"{escape_md(pick.outcome)}"
    )
    lines.append("")
    # Prob real como porcentaje entero (0\.5 → 50%).
    pct_real = round(pick.reference_prob * 100)
    lines.append(f"Prob\\. real \\(Pinnacle de\\-vigged\\): {pct_real}%")
    lines.append(f"Cuota mínima para EV\\+: {_fmt_odds(pick.min_odds_for_value)}")
    pct_ev = round(pick.ev_at_comparison * 100, 1)
    pct_ev_str = escape_md(f"{pct_ev}")
    lines.append(
        f"Mejor cuota EU: `{escape_md(pick.comparison_book)}` "
        f"@ {_fmt_odds(pick.comparison_price)} → EV {pct_ev_str}%"
    )
    lines.append("")
    lines.append("*Verificá tu casa local:*")
    if full_assess.has_value and full_assess.stake_recommended > 0:
        lines.append(
            f"  • Si conseguís ≥ {_fmt_odds(full_th)} → stake "
            f"{fmt_amount(full_assess.stake_recommended)} \\(Kelly/{int(staking.kelly_divisor)}\\)"
        )
    if half_assess.has_value and half_assess.stake_recommended > 0:
        lines.append(
            f"  • Si conseguís ≥ {_fmt_odds(half_th)} → stake "
            f"{fmt_amount(half_assess.stake_recommended)} \\(Kelly reducido\\)"
        )
    lines.append(f"  • Si conseguís < {_fmt_odds(pick.min_odds_for_value)} → DESCARTAR")
    lines.append("")
    lines.append(f"Bankroll vivo: {fmt_amount(pick.bankroll_at_generation)}")
    return "\n".join(lines)


def build_pick_keyboard(pick_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard con 3 botones. `callback_data` incluye pick_id para
    que el wizard sepa sobre qué pick actuar sin estado adicional."""
    if not pick_id or not pick_id.strip():
        raise ValueError("pick_id vacío — no se puede armar el keyboard")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Ya apostada", callback_data=f"{_CB_PREFIX}:placed:{pick_id}"
                ),
                InlineKeyboardButton(
                    "Descartar", callback_data=f"{_CB_PREFIX}:skip:{pick_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Ver detalles",
                    callback_data=f"{_CB_PREFIX}:details:{pick_id}",
                ),
            ],
        ]
    )
