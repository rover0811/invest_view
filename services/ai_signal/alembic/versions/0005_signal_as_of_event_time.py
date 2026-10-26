"""Event time a signal version was generated for.

``gold.signal.as_of_event_time`` is the ``event_time`` of the signal request
that produced the version (article ``published_at`` for NEW_DOCUMENT, move
``window_end`` for PRICE_STATE_CHANGE). The worker coalesces NEW_DOCUMENT
requests that arrive within 10 minutes of event time after it.

Revision ID: 0005_signal_as_of_event_time
Revises: 0004_serving_headline_sources
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_signal_as_of_event_time"
down_revision: str | None = "0004_serving_headline_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal",
        sa.Column("as_of_event_time", sa.DateTime(timezone=True), nullable=True),
        schema="gold",
    )


def downgrade() -> None:
    op.drop_column("signal", "as_of_event_time", schema="gold")
