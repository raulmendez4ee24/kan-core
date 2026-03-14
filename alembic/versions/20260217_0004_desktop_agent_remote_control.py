"""Add desktop agent remote control tables.

Revision ID: 20260217_0004
Revises: 20260217_0003
Create Date: 2026-02-17 00:04:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260217_0004"
down_revision: Union[str, None] = "20260217_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "desktop_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="offline"),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "agent_name",
            name="uq_desktop_agents_org_agent_name",
        ),
    )
    op.create_index(op.f("ix_desktop_agents_organization_id"), "desktop_agents", ["organization_id"], unique=False)
    op.create_index(op.f("ix_desktop_agents_agent_name"), "desktop_agents", ["agent_name"], unique=False)
    op.create_index(op.f("ix_desktop_agents_status"), "desktop_agents", ["status"], unique=False)
    op.create_index(op.f("ix_desktop_agents_last_heartbeat_at"), "desktop_agents", ["last_heartbeat_at"], unique=False)
    op.create_index(op.f("ix_desktop_agents_created_at"), "desktop_agents", ["created_at"], unique=False)
    op.create_index(op.f("ix_desktop_agents_updated_at"), "desktop_agents", ["updated_at"], unique=False)

    op.create_table(
        "desktop_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending_approval"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_desktop_commands_organization_id"), "desktop_commands", ["organization_id"], unique=False)
    op.create_index(op.f("ix_desktop_commands_agent_id"), "desktop_commands", ["agent_id"], unique=False)
    op.create_index(op.f("ix_desktop_commands_action"), "desktop_commands", ["action"], unique=False)
    op.create_index(op.f("ix_desktop_commands_status"), "desktop_commands", ["status"], unique=False)
    op.create_index(op.f("ix_desktop_commands_requested_by_user_id"), "desktop_commands", ["requested_by_user_id"], unique=False)
    op.create_index(op.f("ix_desktop_commands_approved_by_user_id"), "desktop_commands", ["approved_by_user_id"], unique=False)
    op.create_index(op.f("ix_desktop_commands_created_at"), "desktop_commands", ["created_at"], unique=False)
    op.create_index(op.f("ix_desktop_commands_updated_at"), "desktop_commands", ["updated_at"], unique=False)

    op.create_table(
        "desktop_command_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["command_id"], ["desktop_commands.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="uq_desktop_command_results_command_id"),
    )
    op.create_index(op.f("ix_desktop_command_results_organization_id"), "desktop_command_results", ["organization_id"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_agent_id"), "desktop_command_results", ["agent_id"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_command_id"), "desktop_command_results", ["command_id"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_status"), "desktop_command_results", ["status"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_received_at"), "desktop_command_results", ["received_at"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_created_at"), "desktop_command_results", ["created_at"], unique=False)
    op.create_index(op.f("ix_desktop_command_results_updated_at"), "desktop_command_results", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_desktop_command_results_updated_at"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_created_at"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_received_at"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_status"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_command_id"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_agent_id"), table_name="desktop_command_results")
    op.drop_index(op.f("ix_desktop_command_results_organization_id"), table_name="desktop_command_results")
    op.drop_table("desktop_command_results")

    op.drop_index(op.f("ix_desktop_commands_updated_at"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_created_at"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_approved_by_user_id"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_requested_by_user_id"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_status"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_action"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_agent_id"), table_name="desktop_commands")
    op.drop_index(op.f("ix_desktop_commands_organization_id"), table_name="desktop_commands")
    op.drop_table("desktop_commands")

    op.drop_index(op.f("ix_desktop_agents_updated_at"), table_name="desktop_agents")
    op.drop_index(op.f("ix_desktop_agents_created_at"), table_name="desktop_agents")
    op.drop_index(op.f("ix_desktop_agents_last_heartbeat_at"), table_name="desktop_agents")
    op.drop_index(op.f("ix_desktop_agents_status"), table_name="desktop_agents")
    op.drop_index(op.f("ix_desktop_agents_agent_name"), table_name="desktop_agents")
    op.drop_index(op.f("ix_desktop_agents_organization_id"), table_name="desktop_agents")
    op.drop_table("desktop_agents")
