"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create alert_service schema
    op.execute("CREATE SCHEMA IF NOT EXISTS alert_service")

    # users
    op.create_table(
        "users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nickname", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="alert_service",
    )

    # watchlist_items (composite PK)
    op.create_table(
        "watchlist_items",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alert_service.users.user_id"), primary_key=True, nullable=False),
        sa.Column("symbol", sa.Text(), primary_key=True, nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="alert_service",
    )
    op.create_index("ix_watchlist_items_symbol_enabled", "watchlist_items", ["symbol", "notifications_enabled"], schema="alert_service")

    # alert_events
    op.create_table(
        "alert_events",
        sa.Column("alert_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("observation_start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("observation_end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("trigger_values", postgresql.JSONB(), nullable=False),
        sa.Column("source_tick_event_id", sa.Text(), nullable=True),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="alert_events_market_check"),
        sa.CheckConstraint("alert_type IN ('PRICE_ALERT','VI_IMMINENT','MOMENTUM_SHIFT','TRADING_HALT')", name="alert_events_alert_type_check"),
        sa.CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')", name="alert_events_severity_check"),
        schema="alert_service",
    )
    op.create_index("ix_alert_events_symbol_triggered", "alert_events", ["symbol", sa.text("triggered_at DESC")], schema="alert_service")
    op.create_index("ix_alert_events_triggered", "alert_events", [sa.text("triggered_at DESC")], schema="alert_service")

    # notification_events
    op.create_table(
        "notification_events",
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alert_service.users.user_id"), nullable=False),
        sa.Column("alert_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alert_service.alert_events.alert_event_id"), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.Text(), nullable=False),
        sa.Column("delivery_attempted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("delivery_status IN ('PENDING','SENT','FAILED')", name="notification_events_status_check"),
        sa.CheckConstraint("failure_reason IS NULL OR failure_reason IN ('no_connection','send_error')", name="notification_events_failure_reason_check"),
        sa.UniqueConstraint("user_id", "alert_event_id", name="notification_events_user_alert_uq"),
        schema="alert_service",
    )
    op.create_index("ix_notification_events_user_created", "notification_events", ["user_id", sa.text("created_at DESC")], schema="alert_service")
    op.create_index("ix_notification_events_alert", "notification_events", ["alert_event_id"], schema="alert_service")


def downgrade() -> None:
    op.drop_table("notification_events", schema="alert_service")
    op.drop_table("alert_events", schema="alert_service")
    op.drop_table("watchlist_items", schema="alert_service")
    op.drop_table("users", schema="alert_service")
    op.execute("DROP SCHEMA IF EXISTS alert_service CASCADE")
