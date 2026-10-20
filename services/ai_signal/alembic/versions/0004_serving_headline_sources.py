"""Serving symbol name and per-cause source documents (doc 22 15.3, 15.4).

The reader assembles the headline from ``headline_label``, the symbol name and
the live change rate, and REST and MCP read only the ``serving`` schema
(18.7), so the sink stores ``symbol_name`` on the current and history rows.
``why_moved`` lists the documents behind each cause, so the DOCUMENT ids of
every cause are kept in ``serving.symbol_signal_cause_source`` and
``serving.symbol_signal_history_cause_source``; ``source_count`` equals the
number of these rows.

``gold.signal.llm_attempts`` and ``silver.document_judgement.llm_attempts``
keep every model call behind a row as a JSON list of
``{step, model, error, raw_content}`` so answers that broke the prompt rules
can be audited later. Rows decided without a model call keep ``NULL``.

Revision ID: 0004_serving_headline_sources
Revises: 0003_serving_signal_history
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_serving_headline_sources"
down_revision: str | None = "0003_serving_signal_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNAL_TABLES = ("symbol_signal", "symbol_signal_history")
_ATTEMPT_TABLES = (("gold", "signal"), ("silver", "document_judgement"))


def upgrade() -> None:
    for table in _SIGNAL_TABLES:
        # The default only backfills rows written before this revision.
        op.add_column(
            table,
            sa.Column("symbol_name", sa.Text(), nullable=False, server_default=""),
            schema="serving",
        )
        op.alter_column(table, "symbol_name", server_default=None, schema="serving")

    op.create_table(
        "symbol_signal_cause_source",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.ForeignKeyConstraint(
            ["symbol", "rank"],
            ["serving.symbol_signal_cause.symbol", "serving.symbol_signal_cause.rank"],
            ondelete="CASCADE",
        ),
        schema="serving",
    )
    op.create_table(
        "symbol_signal_history_cause_source",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.ForeignKeyConstraint(
            ["move_id", "version", "rank"],
            [
                "serving.symbol_signal_history_cause.move_id",
                "serving.symbol_signal_history_cause.version",
                "serving.symbol_signal_history_cause.rank",
            ],
            ondelete="CASCADE",
        ),
        schema="serving",
    )
    for schema, table in _ATTEMPT_TABLES:
        op.add_column(
            table,
            sa.Column("llm_attempts", postgresql.JSONB(), nullable=True),
            schema=schema,
        )


def downgrade() -> None:
    for schema, table in reversed(_ATTEMPT_TABLES):
        op.drop_column(table, "llm_attempts", schema=schema)
    op.drop_table("symbol_signal_history_cause_source", schema="serving")
    op.drop_table("symbol_signal_cause_source", schema="serving")
    for table in reversed(_SIGNAL_TABLES):
        op.drop_column(table, "symbol_name", schema="serving")
