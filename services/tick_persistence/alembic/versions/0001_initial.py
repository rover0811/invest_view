"""initial bronze silver serving schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")
    op.execute("CREATE SCHEMA IF NOT EXISTS serving")

    op.create_table(
        "tick_history",
        sa.Column("tick_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kafka_topic", sa.Text(), nullable=True),
        sa.Column("kafka_partition", sa.Integer(), nullable=True),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=True),
        sa.Column("tick_dedupe_key", sa.Text(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("change", sa.Integer(), nullable=True),
        sa.Column("open", sa.Integer(), nullable=True),
        sa.Column("high", sa.Integer(), nullable=True),
        sa.Column("low", sa.Integer(), nullable=True),
        sa.Column("ask_price_1", sa.Integer(), nullable=True),
        sa.Column("bid_price_1", sa.Integer(), nullable=True),
        sa.Column("open_vs_price", sa.Integer(), nullable=True),
        sa.Column("high_vs_price", sa.Integer(), nullable=True),
        sa.Column("low_vs_price", sa.Integer(), nullable=True),
        sa.Column("vi_trigger_price", sa.Integer(), nullable=True),
        sa.Column("sell_count", sa.Integer(), nullable=True),
        sa.Column("buy_count", sa.Integer(), nullable=True),
        sa.Column("net_buy_count", sa.Integer(), nullable=True),
        sa.Column("trade_volume", sa.BigInteger(), nullable=True),
        sa.Column("cumulative_volume", sa.BigInteger(), nullable=True),
        sa.Column("cumulative_amount", sa.BigInteger(), nullable=True),
        sa.Column("total_sell_volume", sa.BigInteger(), nullable=True),
        sa.Column("total_buy_volume", sa.BigInteger(), nullable=True),
        sa.Column("ask_remain_1", sa.BigInteger(), nullable=True),
        sa.Column("bid_remain_1", sa.BigInteger(), nullable=True),
        sa.Column("total_ask_remain", sa.BigInteger(), nullable=True),
        sa.Column("total_bid_remain", sa.BigInteger(), nullable=True),
        sa.Column("prev_same_hour_volume", sa.BigInteger(), nullable=True),
        sa.Column("change_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("trade_strength", sa.Numeric(18, 8), nullable=True),
        sa.Column("buy_ratio", sa.Numeric(18, 8), nullable=True),
        sa.Column("prev_day_volume_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("volume_turnover", sa.Numeric(18, 8), nullable=True),
        sa.Column("prev_same_hour_volume_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("vwap", sa.Numeric(20, 8), nullable=True),
        sa.Column("source_tr_id", sa.Text(), nullable=True),
        sa.Column("market", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Text(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trade_time", sa.Text(), nullable=True),
        sa.Column("change_sign", sa.Text(), nullable=True),
        sa.Column("trade_type", sa.Text(), nullable=True),
        sa.Column("open_time", sa.Text(), nullable=True),
        sa.Column("open_vs_sign", sa.Text(), nullable=True),
        sa.Column("high_time", sa.Text(), nullable=True),
        sa.Column("high_vs_sign", sa.Text(), nullable=True),
        sa.Column("low_time", sa.Text(), nullable=True),
        sa.Column("low_vs_sign", sa.Text(), nullable=True),
        sa.Column("business_date", sa.Text(), nullable=True),
        sa.Column("market_session_code", sa.Text(), nullable=True),
        sa.Column("trading_halted", sa.Text(), nullable=True),
        sa.Column("hour_class_code", sa.Text(), nullable=True),
        sa.Column("market_termination_code", sa.Text(), nullable=True),
        sa.Column("persisted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tick_dedupe_key", name="tick_history_dedupe_key_uq"),
        schema="bronze",
    )
    op.create_index("ix_tick_history_symbol_persisted", "tick_history", ["symbol", sa.text("persisted_at DESC")], schema="bronze")
    op.create_index("ix_tick_history_kafka_offset", "tick_history", ["kafka_topic", "kafka_partition", "kafka_offset"], schema="bronze")

    op.create_table(
        "symbol_5m_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("bucket_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("bucket_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("open", sa.Integer(), nullable=True),
        sa.Column("high", sa.Integer(), nullable=True),
        sa.Column("low", sa.Integer(), nullable=True),
        sa.Column("close", sa.Integer(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("vwap", sa.Numeric(20, 8), nullable=True),
        sa.Column("tick_count", sa.Integer(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("symbol", "bucket_start", name="symbol_5m_metrics_symbol_bucket_uq"),
        schema="silver",
    )
    op.create_index("ix_symbol_5m_metrics_symbol_bucket", "symbol_5m_metrics", ["symbol", sa.text("bucket_start DESC")], schema="silver")

    op.create_table(
        "symbol_snapshot",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("last_price", sa.Integer(), nullable=True),
        sa.Column("change", sa.Integer(), nullable=True),
        sa.Column("change_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("change_sign", sa.Text(), nullable=True),
        sa.Column("cumulative_volume", sa.BigInteger(), nullable=True),
        sa.Column("trade_strength", sa.Numeric(18, 8), nullable=True),
        sa.Column("vi_trigger_price", sa.Integer(), nullable=True),
        sa.Column("trading_halted", sa.Text(), nullable=True),
        sa.Column("last_trade_time", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="serving",
    )


def downgrade() -> None:
    op.drop_table("symbol_snapshot", schema="serving")
    op.drop_table("symbol_5m_metrics", schema="silver")
    op.drop_table("tick_history", schema="bronze")
    op.execute("DROP SCHEMA IF EXISTS serving CASCADE")
    op.execute("DROP SCHEMA IF EXISTS silver CASCADE")
    op.execute("DROP SCHEMA IF EXISTS bronze CASCADE")
