"""tick quarantine table for durable poison-pill isolation

Revision ID: 0007_tick_quarantine
Revises: 0006_tick_event_time_contract
Create Date: 2026-06-17

Deterministic poison-pill ticks are isolated into ``bronze.tick_quarantine``
with their raw payload and Kafka lineage instead of being silently skipped.
The unique constraint on the lineage tuple makes re-quarantine on replay a
no-op so the normal bronze append path is never blocked.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_tick_quarantine"
down_revision = "0006_tick_event_time_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tick_quarantine",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("kafka_topic", sa.Text(), nullable=True),
        sa.Column("kafka_partition", sa.Integer(), nullable=True),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "kafka_topic", "kafka_partition", "kafka_offset", name="tick_quarantine_lineage_uq"
        ),
        schema="bronze",
    )
    op.create_index(
        "ix_tick_quarantine_quarantined_at",
        "tick_quarantine",
        [sa.text("quarantined_at DESC")],
        schema="bronze",
    )


def downgrade() -> None:
    op.drop_index("ix_tick_quarantine_quarantined_at", table_name="tick_quarantine", schema="bronze")
    op.drop_table("tick_quarantine", schema="bronze")
