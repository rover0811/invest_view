"""LLM response cache for replays (22).

``gold.llm_cache`` keeps successful completions keyed by a hash of the
request, so a replay does not call the provider again for identical inputs.
Replay cleanup does not delete from this table.

Revision ID: 0008_llm_cache
Revises: 0007_signal_decision
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_llm_cache"
down_revision: str | None = "0007_signal_decision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "hit_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        schema="gold",
    )


def downgrade() -> None:
    op.drop_table("llm_cache", schema="gold")
