"""serving.symbol_signal_timeline cross-schema UNION view

Revision ID: 0003_signal_timeline_view
Revises: 0002_intraday_view
Create Date: 2026-06-01
"""
from alembic import op

revision = "0003_signal_timeline_view"
down_revision = "0002_intraday_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF to_regclass('alert_service.alert_events') IS NULL "
        "OR to_regclass('gold.pattern_events') IS NULL "
        "THEN RAISE EXCEPTION "
        "'signal_timeline view requires alert_service.alert_events and gold.pattern_events to exist first'; "
        "END IF; END $$;"
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW serving.symbol_signal_timeline AS
        SELECT
            symbol,
            'alert' AS event_kind,
            alert_type AS event_type,
            triggered_at::timestamptz AS triggered_at,
            trigger_values,
            severity
        FROM alert_service.alert_events
        UNION ALL
        SELECT
            symbol,
            'pattern' AS event_kind,
            pattern_type AS event_type,
            triggered_at::timestamptz AS triggered_at,
            trigger_values,
            NULL::text AS severity
        FROM gold.pattern_events
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS serving.symbol_signal_timeline")
