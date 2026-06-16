"""tick event id and event time contract columns

Revision ID: 0006_tick_event_time_contract
Revises: 0005_daily_ohlc_view
Create Date: 2026-06-17

``event_ts`` is a nullable normal TIMESTAMPTZ column rather than a generated
stored column. The current append path reflects ORM columns into INSERT values;
T12 will populate ``event_id``/``event_ts`` from the tick payload contract before
switching the conflict target. This migration still best-effort backfills
existing rows from ``business_date`` + ``trade_time`` using the KST market zone.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_tick_event_time_contract"
down_revision = "0005_daily_ohlc_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tick_history", sa.Column("event_id", sa.Text(), nullable=True), schema="bronze")
    op.create_unique_constraint("tick_history_event_id_uq", "tick_history", ["event_id"], schema="bronze")
    op.alter_column(
        "tick_history",
        "received_at",
        existing_type=sa.Text(),
        type_=sa.TIMESTAMP(timezone=True),
        existing_nullable=True,
        postgresql_using="NULLIF(received_at, '')::timestamptz",
        schema="bronze",
    )
    op.add_column("tick_history", sa.Column("event_ts", sa.TIMESTAMP(timezone=True), nullable=True), schema="bronze")
    op.execute(
        """
        UPDATE bronze.tick_history
        SET event_ts = make_timestamptz(
            substring(business_date from 1 for 4)::int,
            substring(business_date from 5 for 2)::int,
            substring(business_date from 7 for 2)::int,
            substring(trade_time from 1 for 2)::int,
            substring(trade_time from 3 for 2)::int,
            substring(trade_time from 5 for 2)::double precision,
            'Asia/Seoul'
        )
        WHERE event_ts IS NULL
          AND business_date ~ '^\\d{8}$'
          AND trade_time ~ '^\\d{6}$'
        """
    )

    op.add_column("symbol_snapshot", sa.Column("business_date", sa.Text(), nullable=True), schema="serving")
    op.add_column("symbol_snapshot", sa.Column("last_event_ts", sa.TIMESTAMP(timezone=True), nullable=True), schema="serving")


def downgrade() -> None:
    op.drop_column("symbol_snapshot", "last_event_ts", schema="serving")
    op.drop_column("symbol_snapshot", "business_date", schema="serving")

    op.drop_column("tick_history", "event_ts", schema="bronze")
    op.alter_column(
        "tick_history",
        "received_at",
        existing_type=sa.TIMESTAMP(timezone=True),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using="received_at::text",
        schema="bronze",
    )
    op.drop_constraint("tick_history_event_id_uq", "tick_history", schema="bronze", type_="unique")
    op.drop_column("tick_history", "event_id", schema="bronze")
