"""Modelos SQLAlchemy 2.0 del schema
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from betting_bot.ids import new_id


class Base(DeclarativeBase):
    pass


class Event(Base):
    """Partido. Identidad cross-system → id UUID v4."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    odds_api_id: Mapped[str | None] = mapped_column(unique=True)
    api_football_id: Mapped[int | None]
    league_key: Mapped[str]
    home_team: Mapped[str]
    away_team: Mapped[str]
    commence_time: Mapped[datetime]
    status: Mapped[str]
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    home_goals_ht: Mapped[int | None]
    away_goals_ht: Mapped[int | None]
    total_corners: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'live', 'finished', 'cancelled', 'postponed')",
            name="ck_events_status",
        ),
        Index("idx_events_commence", "commence_time"),
        Index("idx_events_status", "status"),
        Index("idx_events_league", "league_key"),
    )


class OddsSnapshot(Base):
    """Cuota capturada de una casa para un mercado/outcome en un instante."""

    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    bookmaker_key: Mapped[str]
    market_key: Mapped[str]
    outcome: Mapped[str]
    line: Mapped[float | None]
    price: Mapped[float]
    captured_at: Mapped[datetime]

    __table_args__ = (
        Index("idx_odds_event_market", "event_id", "market_key"),
        Index("idx_odds_captured", "captured_at"),
    )


class Pick(Base):
    """Apuesta de valor detectada. Identidad cross-system → id UUID v4."""

    __tablename__ = "picks"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    market_key: Mapped[str]
    outcome: Mapped[str]
    line: Mapped[float | None]
    reference_book: Mapped[str]
    reference_price: Mapped[float]
    reference_prob: Mapped[float]
    devigging_method: Mapped[str]
    comparison_book: Mapped[str]
    comparison_price: Mapped[float]
    min_odds_for_value: Mapped[float]
    ev_at_comparison: Mapped[float]
    kelly_fraction: Mapped[float]
    stake_recommended: Mapped[int]
    stake_pct_of_bankroll: Mapped[float]
    bankroll_at_generation: Mapped[int]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    generated_at: Mapped[datetime]
    generated_date: Mapped[date]
    placed_at: Mapped[datetime | None]
    actual_book: Mapped[str | None]
    actual_price: Mapped[float | None]
    actual_stake: Mapped[int | None]
    settled_at: Mapped[datetime | None]
    pnl: Mapped[int | None]
    clv: Mapped[float | None]
    closing_pinnacle_price: Mapped[float | None]
    skip_reason: Mapped[str | None]
    notes: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'placed', 'skipped', 'won', 'lost', 'pushed', 'void')",
            name="ck_picks_status",
        ),
        Index("idx_picks_event", "event_id"),
        Index("idx_picks_status", "status"),
        Index("idx_picks_generated", "generated_at"),
        Index(
            "idx_picks_unique_with_line",
            "event_id",
            "market_key",
            "outcome",
            "line",
            "generated_date",
            unique=True,
            sqlite_where=text("line IS NOT NULL"),
        ),
        Index(
            "idx_picks_unique_no_line",
            "event_id",
            "market_key",
            "outcome",
            "generated_date",
            unique=True,
            sqlite_where=text("line IS NULL"),
        ),
    )


class BankrollMovement(Base):
    """Ledger de bankroll: fuente de verdad. Append-only."""

    __tablename__ = "bankroll_movements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    book_code: Mapped[str]
    movement_type: Mapped[str]
    amount: Mapped[int]  # positivo = entra; negativo = sale
    related_pick_id: Mapped[str | None] = mapped_column(ForeignKey("picks.id"))
    notes: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('deposit', 'withdrawal', 'bet_stake', 'bet_payout', 'adjustment')",
            name="ck_bm_movement_type",
        ),
        Index("idx_bm_book", "book_code", "occurred_at"),
        Index("idx_bm_type", "movement_type"),
        Index("idx_bm_pick", "related_pick_id"),
    )


class BankrollBookSnapshot(Base):
    """Snapshot diario por casa. Cache de analytics, NO fuente de verdad."""

    __tablename__ = "bankroll_book_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date]
    book_code: Mapped[str]
    balance: Mapped[int]
    deposits_today: Mapped[int] = mapped_column(server_default=text("0"))
    withdrawals_today: Mapped[int] = mapped_column(server_default=text("0"))
    stakes_today: Mapped[int] = mapped_column(server_default=text("0"))
    payouts_today: Mapped[int] = mapped_column(server_default=text("0"))
    picks_placed_today: Mapped[int] = mapped_column(server_default=text("0"))
    picks_won_today: Mapped[int] = mapped_column(server_default=text("0"))
    picks_lost_today: Mapped[int] = mapped_column(server_default=text("0"))
    pnl_today: Mapped[int] = mapped_column(server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("snapshot_date", "book_code", name="uq_bbs_date_book"),
        Index("idx_bbs_date", "snapshot_date"),
    )


class SystemState(Base):
    """Estado global del sistema. Singleton: siempre id=1."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    is_paused: Mapped[bool] = mapped_column(server_default=text("0"))
    paused_reason: Mapped[str | None]
    paused_at: Mapped[datetime | None]
    last_pipeline_run_at: Mapped[datetime | None]
    last_settlement_run_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (CheckConstraint("id = 1", name="ck_system_state_singleton"),)


class ApiQuotaLog(Base):
    """Tracking de cuota consumida en las APIs externas."""

    __tablename__ = "api_quota_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str]  # 'odds_api' | 'api_football'
    captured_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    requests_remaining: Mapped[int | None]
    requests_used: Mapped[int | None]
    requests_limit: Mapped[int | None]
    endpoint: Mapped[str | None]
    request_id: Mapped[str | None]

    __table_args__ = (Index("idx_aql_provider", "provider", "captured_at"),)


class OperationLog(Base):
    """Log de operaciones del sistema."""

    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))
    level: Mapped[str]
    operation: Mapped[str]
    request_id: Mapped[str | None]
    message: Mapped[str]
    metadata_json: Mapped[str | None] = mapped_column("metadata")

    __table_args__ = (
        Index("idx_oplog_occurred", "occurred_at"),
        Index("idx_oplog_level", "level"),
    )
