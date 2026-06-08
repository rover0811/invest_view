"""silver.symbol_daily_ohlc table for KIS daily/weekly/monthly OHLC bars

Revision ID: 0004_daily_ohlc
Revises: 0003_signal_timeline_view
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_daily_ohlc"
down_revision = "0003_signal_timeline_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "symbol_daily_ohlc",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Integer(), nullable=True),
        sa.Column("high", sa.Integer(), nullable=True),
        sa.Column("low", sa.Integer(), nullable=True),
        sa.Column("close", sa.Integer(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("trade_amount", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("symbol", "interval", "trade_date", name="symbol_daily_ohlc_symbol_interval_date_uq"),
        schema="silver",
    )
    op.create_index(
        "ix_symbol_daily_ohlc_symbol_interval_date",
        "symbol_daily_ohlc",
        ["symbol", "interval", sa.text("trade_date DESC")],
        schema="silver",
    )


def downgrade() -> None:
    op.drop_index("ix_symbol_daily_ohlc_symbol_interval_date", table_name="symbol_daily_ohlc", schema="silver")
    op.drop_table("symbol_daily_ohlc", schema="silver")
