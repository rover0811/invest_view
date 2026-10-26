"""Record every signal generation outcome in gold.signal.

The signal worker publishes ``signal-updates`` for SHOWN, HIDDEN and
NO_CANDIDATE results, and the serving sink orders updates by
``(move_version, version)``. ``gold.signal.version`` is therefore a counter per
move across all outcomes, so HIDDEN and NO_CANDIDATE need a row too:

- ``status``: the published status of that version
- ``decided_by``: nullable, because HIDDEN and NO_CANDIDATE are not decided by a
  generation method (the allowed values stay ``llm | flow_rule | decomposition``)
- ``move_version``: the gold.move version the signal was generated from
- ``input_hash``: hash of the generation input; a redelivered request with the
  same input as the latest version is a no-op

Revision ID: 0002_signal_generation_ledger
Revises: 0001_ai_signal_foundation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_signal_generation_ledger"
down_revision: str | None = "0001_ai_signal_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal",
        sa.Column("status", sa.Text(), nullable=False, server_default="SHOWN"),
        schema="gold",
    )
    op.add_column(
        "signal",
        sa.Column("move_version", sa.Integer(), nullable=True),
        schema="gold",
    )
    op.add_column(
        "signal",
        sa.Column("input_hash", sa.Text(), nullable=True),
        schema="gold",
    )
    op.alter_column("signal", "decided_by", nullable=True, schema="gold")
    op.create_check_constraint(
        "signal_status_ck",
        "signal",
        "status IN ('SHOWN', 'HIDDEN', 'NO_CANDIDATE')",
        schema="gold",
    )
    op.create_check_constraint(
        "signal_shown_decided_by_ck",
        "signal",
        "status <> 'SHOWN' OR decided_by IS NOT NULL",
        schema="gold",
    )


def downgrade() -> None:
    op.drop_constraint("signal_shown_decided_by_ck", "signal", schema="gold")
    op.drop_constraint("signal_status_ck", "signal", schema="gold")
    op.execute("DELETE FROM gold.signal WHERE decided_by IS NULL")
    op.alter_column("signal", "decided_by", nullable=False, schema="gold")
    op.drop_column("signal", "input_hash", schema="gold")
    op.drop_column("signal", "move_version", schema="gold")
    op.drop_column("signal", "status", schema="gold")
