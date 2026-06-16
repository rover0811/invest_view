"""tick_persistence ORM for bronze/silver/serving tables."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    Integer,
    Numeric,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
SCHEMA_SERVING = "serving"


class Base(DeclarativeBase):
    pass


class TickHistory(Base):
    __tablename__: str = "tick_history"
    __table_args__: tuple[object, ...] = (
        UniqueConstraint("tick_dedupe_key", name="tick_history_dedupe_key_uq"),
        UniqueConstraint("event_id", name="tick_history_event_id_uq"),
        Index("ix_tick_history_symbol_persisted", "symbol", text("persisted_at DESC")),
        {"schema": SCHEMA_BRONZE},
    )

    tick_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    kafka_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    kafka_partition: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kafka_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tick_dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ask_price_1: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bid_price_1: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_vs_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high_vs_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low_vs_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vi_trigger_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sell_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buy_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_buy_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    trade_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cumulative_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cumulative_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_sell_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_buy_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ask_remain_1: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bid_remain_1: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_ask_remain: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_bid_remain: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prev_same_hour_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    change_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    trade_strength: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    buy_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    prev_day_volume_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    volume_turnover: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    prev_same_hour_volume_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)

    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    source_tr_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    market: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    trade_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_vs_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_vs_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_vs_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    market_session_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    trading_halted: Mapped[str | None] = mapped_column(Text, nullable=True)
    hour_class_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_termination_code: Mapped[str | None] = mapped_column(Text, nullable=True)

    persisted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class Symbol5mMetrics(Base):
    __tablename__: str = "symbol_5m_metrics"
    __table_args__: tuple[object, ...] = (
        UniqueConstraint("symbol", "bucket_start", name="symbol_5m_metrics_symbol_bucket_uq"),
        Index("ix_symbol_5m_metrics_symbol_bucket", "symbol", text("bucket_start DESC")),
        {"schema": SCHEMA_SILVER},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    bucket_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    open: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    tick_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_final: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class SymbolSnapshot(Base):
    __tablename__: str = "symbol_snapshot"
    __table_args__: tuple[object, ...] = ({"schema": SCHEMA_SERVING},)

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    last_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    change_sign: Mapped[str | None] = mapped_column(Text, nullable=True)
    cumulative_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trade_strength: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    vi_trigger_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trading_halted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_trade_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
