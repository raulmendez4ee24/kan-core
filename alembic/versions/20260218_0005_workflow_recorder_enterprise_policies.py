"""Add workflow recorder + org policies tables.

Revision ID: 20260218_0005
Revises: 20260217_0004
Create Date: 2026-02-18 00:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260218_0005"
down_revision: Union[str, None] = "20260217_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "org_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_org_policies_organization_id"),
    )
    op.create_index(op.f("ix_org_policies_organization_id"), "org_policies", ["organization_id"], unique=False)
    op.create_index(op.f("ix_org_policies_updated_by_user_id"), "org_policies", ["updated_by_user_id"], unique=False)
    op.create_index(op.f("ix_org_policies_created_at"), "org_policies", ["created_at"], unique=False)
    op.create_index(op.f("ix_org_policies_updated_at"), "org_policies", ["updated_at"], unique=False)

    op.create_table(
        "workflow_recordings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False, server_default="Workflow recording"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="recording"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n8n_workflow_id", sa.String(length=128), nullable=True),
        sa.Column("n8n_workflow_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_recordings_organization_id"), "workflow_recordings", ["organization_id"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_agent_id"), "workflow_recordings", ["agent_id"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_status"), "workflow_recordings", ["status"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_created_by_user_id"), "workflow_recordings", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_started_at"), "workflow_recordings", ["started_at"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_created_at"), "workflow_recordings", ["created_at"], unique=False)
    op.create_index(op.f("ix_workflow_recordings_updated_at"), "workflow_recordings", ["updated_at"], unique=False)

    op.create_table(
        "workflow_recording_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("desktop_command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_status", sa.String(length=20), nullable=False, server_default="success"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["recording_id"], ["workflow_recordings.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["desktop_command_id"], ["desktop_commands.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "desktop_command_id",
            name="uq_workflow_recording_steps_recording_command",
        ),
    )
    op.create_index(op.f("ix_workflow_recording_steps_recording_id"), "workflow_recording_steps", ["recording_id"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_organization_id"), "workflow_recording_steps", ["organization_id"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_agent_id"), "workflow_recording_steps", ["agent_id"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_desktop_command_id"), "workflow_recording_steps", ["desktop_command_id"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_action"), "workflow_recording_steps", ["action"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_result_status"), "workflow_recording_steps", ["result_status"], unique=False)
    op.create_index(op.f("ix_workflow_recording_steps_created_at"), "workflow_recording_steps", ["created_at"], unique=False)

    op.create_table(
        "workflow_playbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False, server_default="Workflow playbook"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["recording_id"], ["workflow_recordings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_playbooks_organization_id"), "workflow_playbooks", ["organization_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbooks_agent_id"), "workflow_playbooks", ["agent_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbooks_recording_id"), "workflow_playbooks", ["recording_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbooks_created_at"), "workflow_playbooks", ["created_at"], unique=False)
    op.create_index(op.f("ix_workflow_playbooks_updated_at"), "workflow_playbooks", ["updated_at"], unique=False)

    op.create_table(
        "workflow_playbook_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="running"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["playbook_id"], ["workflow_playbooks.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["desktop_agents.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_playbook_runs_organization_id"), "workflow_playbook_runs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_playbook_id"), "workflow_playbook_runs", ["playbook_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_agent_id"), "workflow_playbook_runs", ["agent_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_status"), "workflow_playbook_runs", ["status"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_created_by_user_id"), "workflow_playbook_runs", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_created_at"), "workflow_playbook_runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_workflow_playbook_runs_updated_at"), "workflow_playbook_runs", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_playbook_runs_updated_at"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_created_at"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_created_by_user_id"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_status"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_agent_id"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_playbook_id"), table_name="workflow_playbook_runs")
    op.drop_index(op.f("ix_workflow_playbook_runs_organization_id"), table_name="workflow_playbook_runs")
    op.drop_table("workflow_playbook_runs")

    op.drop_index(op.f("ix_workflow_playbooks_updated_at"), table_name="workflow_playbooks")
    op.drop_index(op.f("ix_workflow_playbooks_created_at"), table_name="workflow_playbooks")
    op.drop_index(op.f("ix_workflow_playbooks_recording_id"), table_name="workflow_playbooks")
    op.drop_index(op.f("ix_workflow_playbooks_agent_id"), table_name="workflow_playbooks")
    op.drop_index(op.f("ix_workflow_playbooks_organization_id"), table_name="workflow_playbooks")
    op.drop_table("workflow_playbooks")

    op.drop_index(op.f("ix_workflow_recording_steps_created_at"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_result_status"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_action"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_desktop_command_id"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_agent_id"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_organization_id"), table_name="workflow_recording_steps")
    op.drop_index(op.f("ix_workflow_recording_steps_recording_id"), table_name="workflow_recording_steps")
    op.drop_table("workflow_recording_steps")

    op.drop_index(op.f("ix_workflow_recordings_updated_at"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_created_at"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_started_at"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_created_by_user_id"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_status"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_agent_id"), table_name="workflow_recordings")
    op.drop_index(op.f("ix_workflow_recordings_organization_id"), table_name="workflow_recordings")
    op.drop_table("workflow_recordings")

    op.drop_index(op.f("ix_org_policies_updated_at"), table_name="org_policies")
    op.drop_index(op.f("ix_org_policies_created_at"), table_name="org_policies")
    op.drop_index(op.f("ix_org_policies_updated_by_user_id"), table_name="org_policies")
    op.drop_index(op.f("ix_org_policies_organization_id"), table_name="org_policies")
    op.drop_table("org_policies")

