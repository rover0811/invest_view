"""serving.symbol_intraday_5m view over silver 5m metrics

Revision ID: 0002_intraday_view
Revises: 0001_initial
Create Date: 2026-06-01
"""
from alembic import op

revision = "0002_intraday_view"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW serving.symbol_intraday_5m AS
        SELECT
            symbol,
            bucket_start AS ts,
            open,
            high,
            low,
            close,
            volume,
            vwap,
            tick_count,
            300 AS interval_seconds
        FROM silver.symbol_5m_metrics
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS serving.symbol_intraday_5m")
