"""SQLAlchemy 2.x ORM models for alert_service.

All tables live in the ``alert_service`` PostgreSQL schema. CHECK constraints
mirror the alembic migration (T3) exactly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA = "alert_service"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        Index("ix_watchlist_items_symbol_enabled", "symbol", "notifications_enabled"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.user_id"),
        primary_key=True,
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(
        server_default=text("TRUE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        CheckConstraint("market IN ('KRX','NXT')", name="alert_events_market_check"),
        CheckConstraint(
            "alert_type IN ('PRICE_ALERT','VI_IMMINENT','MOMENTUM_SHIFT','TRADING_HALT')",
            name="alert_events_alert_type_check",
        ),
        CheckConstraint(
            "severity IN ('INFO','WARNING','CRITICAL')",
            name="alert_events_severity_check",
        ),
        Index("ix_alert_events_symbol_triggered", "symbol", text("triggered_at DESC")),
        Index("ix_alert_events_triggered", text("triggered_at DESC")),
        {"schema": SCHEMA},
    )

    alert_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    observation_start_at: Mapped[datetime] = mapped_column(nullable=False)
    observation_end_at: Mapped[datetime] = mapped_column(nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(nullable=False)
    trigger_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_tick_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_name: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        CheckConstraint(
            "delivery_status IN ('PENDING','SENT','FAILED')",
            name="notification_events_status_check",
        ),
        CheckConstraint(
            "failure_reason IS NULL OR failure_reason IN ('no_connection','send_error')",
            name="notification_events_failure_reason_check",
        ),
        UniqueConstraint("user_id", "alert_event_id", name="notification_events_user_alert_uq"),
        Index("ix_notification_events_user_created", "user_id", text("created_at DESC")),
        Index("ix_notification_events_alert", "alert_event_id"),
        {"schema": SCHEMA},
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.user_id"),
        nullable=False,
    )
    alert_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.alert_events.alert_event_id"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_attempted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
