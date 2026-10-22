"""Article character on document judgements.

``silver.document_judgement`` gains ``info_type``, ``event_subject``,
``event_type``, ``event_object`` and ``target_relevance`` (document_judgement
v4). The processor gates signal regeneration on them; the signal generation
input carries them for every candidate. Rows judged before v4 keep NULL.

Revision ID: 0006_judgement_character
Revises: 0005_signal_as_of_event_time
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_judgement_character"
down_revision: str | None = "0005_signal_as_of_event_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INFO_TYPES = ("NEW_FACT", "PRICE_RECAP", "OPINION", "FLOW", "OTHER")
EVENT_TYPES = (
    "CONTRACT",
    "EARNINGS",
    "INVESTMENT",
    "TECH_ANNOUNCEMENT",
    "PRODUCT",
    "PERSONNEL",
    "POLICY",
    "REGULATION",
    "LEGAL",
    "CAPITAL",
    "SCHEDULE",
    "OTHER",
)
TARGET_RELEVANCES = ("DIRECT", "COMPETITOR", "SUPPLY_CHAIN", "SECTOR", "UNRELATED")
ENUM_COLUMNS = (
    ("info_type", INFO_TYPES),
    ("event_type", EVENT_TYPES),
    ("target_relevance", TARGET_RELEVANCES),
)
COLUMNS = (
    "info_type",
    "event_subject",
    "event_type",
    "event_object",
    "target_relevance",
)


def _check(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IS NULL OR {column} IN ({quoted})"


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            "document_judgement",
            sa.Column(column, sa.Text(), nullable=True),
            schema="silver",
        )
    for column, values in ENUM_COLUMNS:
        op.create_check_constraint(
            f"document_judgement_{column}_ck",
            "document_judgement",
            _check(column, values),
            schema="silver",
        )


def downgrade() -> None:
    for column, _ in ENUM_COLUMNS:
        op.drop_constraint(
            f"document_judgement_{column}_ck",
            "document_judgement",
            type_="check",
            schema="silver",
        )
    for column in reversed(COLUMNS):
        op.drop_column("document_judgement", column, schema="silver")
