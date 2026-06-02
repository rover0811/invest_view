"""event_pattern_persistence ORM. gold.pattern_events mirrors alert_service.alert_events shape; triggered_at is TIMESTAMPTZ for the signal_timeline UNION."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA = "gold"


class Base(DeclarativeBase):
    pass


class PatternEvent(Base):
    __tablename__ = "pattern_events"
    __table_args__ = (
        Index("ix_pattern_events_symbol_triggered", "symbol", text("triggered_at DESC")),
        {"schema": SCHEMA},
    )

    pattern_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_type: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    trigger_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    strategy_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_tick_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
