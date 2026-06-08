"""SQLAlchemy 2.x ORM models for alert_service.

All tables live in the ``alert_service`` PostgreSQL schema. CHECK constraints
mirror the alembic migration (T3) exactly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DDL, ForeignKey, Index, Text, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA = "alert_service"
AGENT_SCHEMA = "agent"


class Base(DeclarativeBase):
    pass


event.listen(Base.metadata, "before_create", DDL(f"CREATE SCHEMA IF NOT EXISTS {AGENT_SCHEMA}"))


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


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index(
            "idx_sessions_user_active",
            "user_id",
            text("updated_at DESC"),
            postgresql_where=text("is_archived = false"),
        ),
        {"schema": AGENT_SCHEMA},
    )

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.user_id"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(
        server_default=text("false"), nullable=False
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('streaming','complete','interrupted','error')",
            name="chat_messages_status_check",
        ),
        Index("idx_messages_session_parent", "session_id", "parent_id"),
        {"schema": AGENT_SCHEMA},
    )

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{AGENT_SCHEMA}.chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{AGENT_SCHEMA}.chat_messages.message_id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, server_default=text("'complete'"), nullable=False
    )
    tool_trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
