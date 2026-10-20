"""Serving ordering keys and per-version history for the serving sink.

The ``signal-updates`` contract orders updates by ``(move_version, version)``
within a move and by ``move_window_end`` across moves of the same symbol, so
``serving.symbol_signal`` stores both keys. ``generation_method`` is stored
as free text so values the reader does not use yet (``flow_rule``,
``decomposition``) are accepted (18.10).

REST and MCP read only the ``serving`` schema (18.7), and the version history
by date needs every published version, so the sink also appends to
``serving.symbol_signal_history`` and ``serving.symbol_signal_history_cause``
keyed by ``(move_id, version)``.

Revision ID: 0003_serving_signal_history
Revises: 0002_signal_generation_ledger
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_serving_signal_history"
down_revision: str | None = "0002_signal_generation_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CK = "status IN ('SHOWN', 'HIDDEN', 'NO_CANDIDATE')"


def upgrade() -> None:
    op.add_column(
        "symbol_signal",
        sa.Column("move_version", sa.Integer(), nullable=False),
        schema="serving",
    )
    op.add_column(
        "symbol_signal",
        sa.Column("move_window_end", sa.DateTime(timezone=True), nullable=False),
        schema="serving",
    )
    op.add_column(
        "symbol_signal",
        sa.Column("generation_method", sa.Text(), nullable=True),
        schema="serving",
    )
    op.create_check_constraint(
        "symbol_signal_move_version_ck",
        "symbol_signal",
        "move_version >= 1",
        schema="serving",
    )

    op.create_table(
        "symbol_signal_history",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("move_version", sa.Integer(), nullable=False),
        sa.Column("move_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("headline_label", sa.Text(), nullable=True),
        sa.Column("generation_method", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="symbol_signal_history_version_ck"),
        sa.CheckConstraint(
            "move_version >= 1", name="symbol_signal_history_move_version_ck"
        ),
        sa.CheckConstraint(_STATUS_CK, name="symbol_signal_history_status_ck"),
        schema="serving",
    )
    op.create_index(
        "symbol_signal_history_symbol_window_idx",
        "symbol_signal_history",
        ["symbol", "move_window_end"],
        schema="serving",
    )
    op.create_table(
        "symbol_signal_history_cause",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["move_id", "version"],
            [
                "serving.symbol_signal_history.move_id",
                "serving.symbol_signal_history.version",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "rank BETWEEN 1 AND 3", name="symbol_signal_history_cause_rank_ck"
        ),
        sa.CheckConstraint(
            "source_count >= 1", name="symbol_signal_history_cause_source_count_ck"
        ),
        schema="serving",
    )


def downgrade() -> None:
    op.drop_table("symbol_signal_history_cause", schema="serving")
    op.drop_index(
        "symbol_signal_history_symbol_window_idx",
        table_name="symbol_signal_history",
        schema="serving",
    )
    op.drop_table("symbol_signal_history", schema="serving")
    op.drop_constraint(
        "symbol_signal_move_version_ck", "symbol_signal", schema="serving"
    )
    op.drop_column("symbol_signal", "generation_method", schema="serving")
    op.drop_column("symbol_signal", "move_window_end", schema="serving")
    op.drop_column("symbol_signal", "move_version", schema="serving")
