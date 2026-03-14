"""Add Task Studio + evidence + execution queue tables.

Revision ID: 20260218_0006
Revises: 20260218_0005
Create Date: 2026-02-18 00:06:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260218_0006"
down_revision: Union[str, None] = "20260218_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Desktop agents get an environment label (sandbox vs production).
    op.add_column(
        "desktop_agents",
        sa.Column(
            "environment",
            sa.String(length=16),
            nullable=False,
            server_default="production",
        ),
    )
    op.create_index(op.f("ix_desktop_agents_environment"), "desktop_agents", ["environment"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("schedule_cron", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("target_env", sa.String(length=16), nullable=False, server_default="production"),
        sa.Column("require_sandbox_pass", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capture_evidence", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_organization_id"), "tasks", ["organization_id"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_target_env"), "tasks", ["target_env"], unique=False)
    op.create_index(op.f("ix_tasks_capture_evidence"), "tasks", ["capture_evidence"], unique=False)
    op.create_index(op.f("ix_tasks_created_by_user_id"), "tasks", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_tasks_created_at"), "tasks", ["created_at"], unique=False)
    op.create_index(op.f("ix_tasks_updated_at"), "tasks", ["updated_at"], unique=False)
    op.create_index(
        "ix_tasks_org_status_updated",
        "tasks",
        ["organization_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "task_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
        sa.Column("environment", sa.String(length=16), nullable=False, server_default="production"),
        sa.Column("triggered_by", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_runs_organization_id"), "task_runs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_task_runs_task_id"), "task_runs", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_runs_status"), "task_runs", ["status"], unique=False)
    op.create_index(op.f("ix_task_runs_environment"), "task_runs", ["environment"], unique=False)
    op.create_index(op.f("ix_task_runs_triggered_by"), "task_runs", ["triggered_by"], unique=False)
    op.create_index(op.f("ix_task_runs_requested_by_user_id"), "task_runs", ["requested_by_user_id"], unique=False)
    op.create_index(op.f("ix_task_runs_correlation_id"), "task_runs", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_task_runs_started_at"), "task_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_task_runs_finished_at"), "task_runs", ["finished_at"], unique=False)
    op.create_index(op.f("ix_task_runs_created_at"), "task_runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_task_runs_updated_at"), "task_runs", ["updated_at"], unique=False)

    op.create_table(
        "evidence_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("bucket", sa.String(length=120), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket", "path", name="uq_evidence_assets_bucket_path"),
    )
    op.create_index(op.f("ix_evidence_assets_organization_id"), "evidence_assets", ["organization_id"], unique=False)
    op.create_index(op.f("ix_evidence_assets_run_id"), "evidence_assets", ["run_id"], unique=False)
    op.create_index(op.f("ix_evidence_assets_step_id"), "evidence_assets", ["step_id"], unique=False)
    op.create_index(op.f("ix_evidence_assets_kind"), "evidence_assets", ["kind"], unique=False)
    op.create_index(op.f("ix_evidence_assets_sha256"), "evidence_assets", ["sha256"], unique=False)
    op.create_index(op.f("ix_evidence_assets_redacted"), "evidence_assets", ["redacted"], unique=False)
    op.create_index(op.f("ix_evidence_assets_expires_at"), "evidence_assets", ["expires_at"], unique=False)
    op.create_index(op.f("ix_evidence_assets_created_at"), "evidence_assets", ["created_at"], unique=False)

    op.create_table(
        "task_run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=True),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("evidence_before_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_after_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"]),
        sa.ForeignKeyConstraint(["evidence_before_asset_id"], ["evidence_assets.id"]),
        sa.ForeignKeyConstraint(["evidence_after_asset_id"], ["evidence_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_run_steps_organization_id"), "task_run_steps", ["organization_id"], unique=False)
    op.create_index(op.f("ix_task_run_steps_run_id"), "task_run_steps", ["run_id"], unique=False)
    op.create_index(op.f("ix_task_run_steps_step_order"), "task_run_steps", ["step_order"], unique=False)
    op.create_index(op.f("ix_task_run_steps_kind"), "task_run_steps", ["kind"], unique=False)
    op.create_index(op.f("ix_task_run_steps_action"), "task_run_steps", ["action"], unique=False)
    op.create_index(op.f("ix_task_run_steps_status"), "task_run_steps", ["status"], unique=False)
    op.create_index(op.f("ix_task_run_steps_attempts"), "task_run_steps", ["attempts"], unique=False)
    op.create_index(op.f("ix_task_run_steps_started_at"), "task_run_steps", ["started_at"], unique=False)
    op.create_index(op.f("ix_task_run_steps_completed_at"), "task_run_steps", ["completed_at"], unique=False)
    op.create_index(op.f("ix_task_run_steps_evidence_before_asset_id"), "task_run_steps", ["evidence_before_asset_id"], unique=False)
    op.create_index(op.f("ix_task_run_steps_evidence_after_asset_id"), "task_run_steps", ["evidence_after_asset_id"], unique=False)
    op.create_index(op.f("ix_task_run_steps_created_at"), "task_run_steps", ["created_at"], unique=False)
    op.create_index(op.f("ix_task_run_steps_updated_at"), "task_run_steps", ["updated_at"], unique=False)

    op.create_table(
        "execution_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("leased_by", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_execution_jobs_organization_id"), "execution_jobs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_execution_jobs_kind"), "execution_jobs", ["kind"], unique=False)
    op.create_index(op.f("ix_execution_jobs_status"), "execution_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_execution_jobs_run_after"), "execution_jobs", ["run_after"], unique=False)
    op.create_index(op.f("ix_execution_jobs_leased_by"), "execution_jobs", ["leased_by"], unique=False)
    op.create_index(op.f("ix_execution_jobs_lease_expires_at"), "execution_jobs", ["lease_expires_at"], unique=False)
    op.create_index(op.f("ix_execution_jobs_attempts"), "execution_jobs", ["attempts"], unique=False)
    op.create_index(op.f("ix_execution_jobs_created_at"), "execution_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_execution_jobs_updated_at"), "execution_jobs", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_execution_jobs_updated_at"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_created_at"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_attempts"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_lease_expires_at"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_leased_by"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_run_after"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_status"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_kind"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_organization_id"), table_name="execution_jobs")
    op.drop_table("execution_jobs")

    op.drop_index(op.f("ix_task_run_steps_updated_at"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_created_at"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_evidence_after_asset_id"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_evidence_before_asset_id"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_completed_at"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_started_at"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_attempts"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_status"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_action"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_kind"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_step_order"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_run_id"), table_name="task_run_steps")
    op.drop_index(op.f("ix_task_run_steps_organization_id"), table_name="task_run_steps")
    op.drop_table("task_run_steps")

    op.drop_index(op.f("ix_evidence_assets_created_at"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_expires_at"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_redacted"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_sha256"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_kind"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_step_id"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_run_id"), table_name="evidence_assets")
    op.drop_index(op.f("ix_evidence_assets_organization_id"), table_name="evidence_assets")
    op.drop_table("evidence_assets")

    op.drop_index(op.f("ix_task_runs_updated_at"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_created_at"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_finished_at"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_started_at"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_correlation_id"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_requested_by_user_id"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_triggered_by"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_environment"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_status"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_task_id"), table_name="task_runs")
    op.drop_index(op.f("ix_task_runs_organization_id"), table_name="task_runs")
    op.drop_table("task_runs")

    op.drop_index("ix_tasks_org_status_updated", table_name="tasks")
    op.drop_index(op.f("ix_tasks_updated_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_created_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_created_by_user_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_capture_evidence"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_target_env"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_organization_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_index(op.f("ix_desktop_agents_environment"), table_name="desktop_agents")
    op.drop_column("desktop_agents", "environment")

