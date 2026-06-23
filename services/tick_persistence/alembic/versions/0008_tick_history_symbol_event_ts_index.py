"""add tick_history symbol event_ts hydration index

Revision ID: 0008_tick_history_symbol_event_ts_index
Revises: 0007_tick_quarantine
Create Date: 2026-06-23

The 5-minute bar hydration path reads bronze.tick_history by symbol and event_ts
range. Without this index, every new bucket can scan the full append-only bronze
table per active symbol, which blocks tick-persistence transactions during market
hours.
"""

from alembic import op

revision = "0008_tick_history_symbol_event_ts_index"
down_revision = "0007_tick_quarantine"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_tick_history_symbol_event_ts"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME}
            ON bronze.tick_history (symbol, event_ts)
            WHERE event_ts IS NOT NULL
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS bronze.{_INDEX_NAME}")
