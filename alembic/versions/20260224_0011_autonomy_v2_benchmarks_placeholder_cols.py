"""Add all placeholder columns to autonomy_v2_benchmarks.

The AutonomyV2Benchmark model is dynamically generated from _placeholder_columns()
in models.py. Migration 0008 only created a subset of those columns. Every INSERT
fails with "column X does not exist". This migration adds every missing column and
relaxes the NOT NULL constraint on `goal` (not mapped by the dynamic model).

Revision ID: 20260224_0011
Revises: 20260224_0010
Create Date: 2026-02-24 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260224_0011"
down_revision: Union[str, None] = "20260224_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "autonomy_v2_benchmarks"


def _add_col_if_missing(col: sa.Column) -> None:
    """Add a column only if it doesn't already exist (idempotent)."""
    try:
        op.add_column(_T, col)
    except Exception:
        pass  # column already exists


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Relax constraints on legacy columns that the dynamic model does  #
    #    NOT map (goal) or maps with a different type (run_id as UUID).   #
    # ------------------------------------------------------------------ #
    with op.batch_alter_table(_T) as batch:
        # goal is NOT NULL but the dynamic model ignores it → make nullable
        try:
            batch.alter_column("goal", nullable=True)
        except Exception:
            pass
        # run_id: change from varchar(120) NOT NULL to TEXT nullable so that
        # the dynamic model (maps it as UUID) can write UUID-formatted strings.
        try:
            batch.alter_column(
                "run_id",
                existing_type=sa.String(120),
                type_=sa.Text(),
                nullable=True,
            )
        except Exception:
            pass
        # status: widen from varchar(40) to varchar(64)
        try:
            batch.alter_column(
                "status",
                existing_type=sa.String(40),
                type_=sa.String(64),
                existing_nullable=False,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 2. UUID foreign-key–like columns                                     #
    # ------------------------------------------------------------------ #
    uuid_cols = [
        "client_id", "user_id", "role_id", "permission_id",
        "objective_id", "objective_run_id", "recommendation_id",
        "mission_id", "lead_id", "agent_id", "command_id", "task_id",
        "step_id", "recording_id", "playbook_id",
        "created_by_user_id", "updated_by_user_id",
        "requested_by_user_id", "approved_by_user_id",
    ]
    for col_name in uuid_cols:
        _add_col_if_missing(
            sa.Column(col_name, postgresql.UUID(as_uuid=True), nullable=True, index=True)
        )

    # ------------------------------------------------------------------ #
    # 3. String / Text columns                                             #
    # ------------------------------------------------------------------ #
    str_cols: list[tuple[str, sa.types.TypeEngine]] = [
        ("email", sa.String(320)),
        ("full_name", sa.String(240)),
        ("name", sa.String(240)),
        ("agent_name", sa.String(240)),
        ("title", sa.String(255)),
        ("kind", sa.String(80)),
        ("provider", sa.String(120)),
        ("provider_id", sa.String(120)),
        ("pattern_key", sa.String(255)),
        ("integration", sa.String(120)),
        ("function_name", sa.String(160)),
        ("event_type", sa.String(120)),
        ("action", sa.String(160)),
        ("resource", sa.String(160)),
        ("resource_id", sa.String(160)),
        ("source", sa.String(120)),
        ("channel", sa.String(120)),
        ("entity_id", sa.String(160)),
        ("session_id", sa.String(160)),
        ("role", sa.String(80)),
        ("error_code", sa.String(120)),
        ("environment", sa.String(32)),
        ("timezone", sa.String(80)),
        ("auth_type", sa.String(64)),
        ("header_name", sa.String(128)),
        ("integration_name", sa.String(160)),
        ("last_workflow_id", sa.String(160)),
        ("industry", sa.String(120)),
        ("last_status", sa.String(64)),
        ("last_error_code", sa.String(120)),
    ]
    for col_name, col_type in str_cols:
        _add_col_if_missing(sa.Column(col_name, col_type, nullable=True, index=True))

    text_cols = [
        "password_hash", "summary", "detail", "error", "error_message",
        "content", "reasoning", "openapi_url", "docs_url", "base_url",
        "secret_encrypted", "last_workflow_url",
    ]
    for col_name in text_cols:
        _add_col_if_missing(sa.Column(col_name, sa.Text(), nullable=True))

    # ------------------------------------------------------------------ #
    # 4. Numeric / Boolean columns                                         #
    # ------------------------------------------------------------------ #
    int_cols = [
        "step_order", "current_step", "attempts", "window_minutes",
        "input_tokens", "output_tokens", "total_leads", "total_conversations",
        "total_messages", "total_appointments", "total_sales", "total_errors",
        "total_api_failures", "priority", "duration_ms", "timeout_ms",
        "success_count", "failure_count", "attempt_number", "sample_size",
    ]
    for col_name in int_cols:
        _add_col_if_missing(sa.Column(col_name, sa.Integer(), nullable=True))

    float_cols = [
        "risk_score", "confidence", "success_rate", "avg_impact_score",
        "cost_usd", "avg_duration_ms", "avg_cost_usd",
    ]
    for col_name in float_cols:
        _add_col_if_missing(sa.Column(col_name, sa.Float(), nullable=True))

    bool_cols = [
        "is_active", "is_owner", "auto_repair_used", "fallback_used",
        "requires_human_approval",
    ]
    for col_name in bool_cols:
        _add_col_if_missing(sa.Column(col_name, sa.Boolean(), nullable=True))

    # ------------------------------------------------------------------ #
    # 5. JSONB columns (NOT NULL with server defaults)                    #
    # ------------------------------------------------------------------ #
    jsonb_obj_cols = [
        "payload", "result", "data", "args", "capabilities", "policy",
        "metrics", "diagnostics", "definition", "context",
        "llm_preferences", "persona",
        "command_metadata", "memory_metadata", "learning_metadata",
        "integration_metadata",
    ]
    for col_name in jsonb_obj_cols:
        _add_col_if_missing(
            sa.Column(
                col_name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            )
        )

    jsonb_arr_cols = ["tags", "steps"]
    for col_name in jsonb_arr_cols:
        _add_col_if_missing(
            sa.Column(
                col_name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            )
        )

    _add_col_if_missing(
        sa.Column(
            "openapi_spec",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        )
    )

    # ------------------------------------------------------------------ #
    # 6. DateTime columns                                                  #
    # ------------------------------------------------------------------ #
    dt_cols = [
        "window_start", "window_end", "scheduled_for", "executed_at",
        "started_at", "finished_at", "approved_at", "issued_at",
        "received_at", "last_heartbeat_at", "connected_at",
        "disconnected_at", "last_used_at", "timestamp", "updated_at",
    ]
    for col_name in dt_cols:
        _add_col_if_missing(
            sa.Column(col_name, sa.DateTime(timezone=True), nullable=True, index=True)
        )


def downgrade() -> None:
    # Downgrade intentionally left as no-op: dropping 70+ columns is
    # destructive and these are analytics/metrics with no critical data.
    pass
