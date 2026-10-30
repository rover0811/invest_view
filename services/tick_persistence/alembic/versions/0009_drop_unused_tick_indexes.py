"""drop unused bronze.tick_history indexes

Revision ID: 0009_drop_unused_tick_indexes
Revises: 0008_tick_symbol_event_ts_idx
Create Date: 2026-10-30

bronze.tick_history is append-only and grew to 51GB (21GB of it indexes) on a
single-node homelab, which put the node under DiskPressure. Three indexes are
not used by any read or write path:

- tick_history_pkey (tick_id): no query looks rows up by tick_id.
- tick_history_dedupe_key_uq (tick_dedupe_key): legacy Kafka-offset dedupe,
  superseded by tick_history_event_id_uq as the ON CONFLICT target in 0006.
- ix_tick_history_kafka_offset: lineage columns are written but never filtered on.

The remaining indexes cover every access path: event_id_uq (insert idempotency),
symbol_persisted (tick-detail API), symbol_event_ts (5m bar hydration).

The production database already had these dropped manually, so every statement
uses IF EXISTS.
"""

from alembic import op

revision = "0009_drop_unused_tick_indexes"
down_revision = "0008_tick_symbol_event_ts_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bronze.tick_history DROP CONSTRAINT IF EXISTS tick_history_pkey")
    op.execute("ALTER TABLE bronze.tick_history DROP CONSTRAINT IF EXISTS tick_history_dedupe_key_uq")
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS bronze.ix_tick_history_kafka_offset")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE bronze.tick_history ADD CONSTRAINT tick_history_pkey PRIMARY KEY (tick_id)"
    )
    op.execute(
        "ALTER TABLE bronze.tick_history "
        "ADD CONSTRAINT tick_history_dedupe_key_uq UNIQUE (tick_dedupe_key)"
    )
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tick_history_kafka_offset
            ON bronze.tick_history (kafka_topic, kafka_partition, kafka_offset)
            """
        )
