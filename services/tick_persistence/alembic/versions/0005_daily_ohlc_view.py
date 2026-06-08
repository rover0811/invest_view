"""serving.symbol_daily_ohlc view over silver.symbol_daily_ohlc

Revision ID: 0005_daily_ohlc_view
Revises: 0004_daily_ohlc
Create Date: 2026-06-04
"""
from alembic import op

revision = "0005_daily_ohlc_view"
down_revision = "0004_daily_ohlc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW serving.symbol_daily_ohlc AS
        SELECT
            symbol,
            interval,
            trade_date,
            open,
            high,
            low,
            close,
            volume,
            trade_amount,
            source,
            fetched_at
        FROM silver.symbol_daily_ohlc
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS serving.symbol_daily_ohlc")
