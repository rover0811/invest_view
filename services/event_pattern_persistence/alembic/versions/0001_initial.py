"""initial gold schema

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
    op.execute("CREATE SCHEMA IF NOT EXISTS gold")

    op.create_table(
        "pattern_events",
        sa.Column("pattern_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=True),
        sa.Column("pattern_type", sa.Text(), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("window_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("trigger_values", postgresql.JSONB(), nullable=False),
        sa.Column("strategy_name", sa.Text(), nullable=True),
        sa.Column("source_tick_event_id", sa.Text(), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="gold",
    )
    op.create_index(
        "ix_pattern_events_symbol_triggered",
        "pattern_events",
        ["symbol", sa.text("triggered_at DESC")],
        schema="gold",
    )


def downgrade() -> None:
    op.drop_table("pattern_events", schema="gold")
    op.execute("DROP SCHEMA IF EXISTS gold CASCADE")
