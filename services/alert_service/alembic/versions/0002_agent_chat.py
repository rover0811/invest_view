"""agent chat schema

Revision ID: 0002_agent_chat
Revises: 0001_initial
Create Date: 2026-06-03
"""
# pyright: reportAttributeAccessIssue=false
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_agent_chat"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")

    op.create_table(
        "chat_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_service.users.user_id"),
            nullable=False,
        ),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="agent",
    )
    op.create_index(
        "idx_sessions_user_active",
        "chat_sessions",
        ["user_id", sa.text("updated_at DESC")],
        schema="agent",
        postgresql_where=sa.text("is_archived = false"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent.chat_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent.chat_messages.message_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'complete'")),
        sa.Column("tool_trace", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('streaming','complete','interrupted','error')",
            name="chat_messages_status_check",
        ),
        schema="agent",
    )
    op.create_index(
        "idx_messages_session_parent",
        "chat_messages",
        ["session_id", "parent_id"],
        schema="agent",
    )


def downgrade() -> None:
    op.drop_table("chat_messages", schema="agent")
    op.drop_table("chat_sessions", schema="agent")
    op.execute("DROP SCHEMA IF EXISTS agent CASCADE")
