"""KEEP or UPDATE decisions of signal regeneration.

A NEW_DOCUMENT request may keep the current signal text. A KEEP answer writes
no ``gold.signal`` version, so ``gold.signal_decision`` records the decision,
its reason and whether the deterministic guard turned an UPDATE into KEEP.

Revision ID: 0007_signal_decision
Revises: 0006_judgement_character
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_signal_decision"
down_revision: str | None = "0006_judgement_character"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_decision",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("move_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("move_version", sa.Integer(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("guard_applied", sa.Boolean(), nullable=False),
        sa.Column("llm_attempts", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision IN ('KEEP', 'UPDATE')", name="ck_signal_decision_decision"
        ),
        schema="gold",
    )
    op.create_index(
        "ix_signal_decision_move",
        "signal_decision",
        ["move_id", "move_version"],
        schema="gold",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_signal_decision_move", table_name="signal_decision", schema="gold"
    )
    op.drop_table("signal_decision", schema="gold")
