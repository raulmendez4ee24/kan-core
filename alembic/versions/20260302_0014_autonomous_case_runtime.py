"""Add autonomous case runtime tables.

Revision ID: 20260302_0014
Revises: 20260301_0013
Create Date: 2026-03-02 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260302_0014"
down_revision: Union[str, None] = "20260301_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if not _has_table("autonomous_cases"):
        op.create_table(
            "autonomous_cases",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("case_type", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
            sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("risk_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("current_goal", sa.Text(), nullable=False),
            sa.Column("business_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("world_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("last_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("last_execution_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("resolution_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_autonomous_cases_organization_id", "autonomous_cases", ["organization_id"])
        op.create_index("ix_autonomous_cases_case_type", "autonomous_cases", ["case_type"])
        op.create_index("ix_autonomous_cases_status", "autonomous_cases", ["status"])
        op.create_index("ix_autonomous_cases_created_at", "autonomous_cases", ["created_at"])
        op.create_index("ix_autonomous_cases_updated_at", "autonomous_cases", ["updated_at"])

    if not _has_table("autonomous_case_steps"):
        op.create_table(
            "autonomous_case_steps",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("step_index", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="planned"),
            sa.Column("decision_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("execution_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("execution_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("outcome_evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["autonomous_cases.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_autonomous_case_steps_case_id", "autonomous_case_steps", ["case_id"])
        op.create_index("ix_autonomous_case_steps_organization_id", "autonomous_case_steps", ["organization_id"])
        op.create_index("ix_autonomous_case_steps_created_at", "autonomous_case_steps", ["created_at"])

    if not _has_table("business_state_snapshots"):
        op.create_table(
            "business_state_snapshots",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("snapshot_kind", sa.String(length=64), nullable=False, server_default="operational"),
            sa.Column("state_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_business_state_snapshots_organization_id", "business_state_snapshots", ["organization_id"])
        op.create_index("ix_business_state_snapshots_snapshot_kind", "business_state_snapshots", ["snapshot_kind"])
        op.create_index("ix_business_state_snapshots_window_start", "business_state_snapshots", ["window_start"])
        op.create_index("ix_business_state_snapshots_window_end", "business_state_snapshots", ["window_end"])
        op.create_index("ix_business_state_snapshots_created_at", "business_state_snapshots", ["created_at"])

    if not _has_table("operation_memory_entries"):
        op.create_table(
            "operation_memory_entries",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("memory_type", sa.String(length=64), nullable=False),
            sa.Column("signature", sa.String(length=160), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("success", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["autonomous_cases.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_operation_memory_entries_organization_id", "operation_memory_entries", ["organization_id"])
        op.create_index("ix_operation_memory_entries_case_id", "operation_memory_entries", ["case_id"])
        op.create_index("ix_operation_memory_entries_memory_type", "operation_memory_entries", ["memory_type"])
        op.create_index("ix_operation_memory_entries_signature", "operation_memory_entries", ["signature"])
        op.create_index("ix_operation_memory_entries_created_at", "operation_memory_entries", ["created_at"])

    if not _has_table("execution_leases"):
        op.create_table(
            "execution_leases",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("lease_key", sa.String(length=160), nullable=False),
            sa.Column("owner", sa.String(length=120), nullable=False),
            sa.Column("leased_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["autonomous_cases.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "lease_key", name="uq_execution_leases_org_key"),
        )
        op.create_index("ix_execution_leases_organization_id", "execution_leases", ["organization_id"])
        op.create_index("ix_execution_leases_case_id", "execution_leases", ["case_id"])
        op.create_index("ix_execution_leases_lease_key", "execution_leases", ["lease_key"])
        op.create_index("ix_execution_leases_owner", "execution_leases", ["owner"])
        op.create_index("ix_execution_leases_leased_until", "execution_leases", ["leased_until"])
        op.create_index("ix_execution_leases_created_at", "execution_leases", ["created_at"])
        op.create_index("ix_execution_leases_updated_at", "execution_leases", ["updated_at"])

    if not _has_table("idempotency_records"):
        op.create_table(
            "idempotency_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False),
            sa.Column("step_hash", sa.String(length=200), nullable=False),
            sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["autonomous_cases.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_idempotency_records_org_key"),
        )
        op.create_index("ix_idempotency_records_organization_id", "idempotency_records", ["organization_id"])
        op.create_index("ix_idempotency_records_case_id", "idempotency_records", ["case_id"])
        op.create_index("ix_idempotency_records_idempotency_key", "idempotency_records", ["idempotency_key"])
        op.create_index("ix_idempotency_records_step_hash", "idempotency_records", ["step_hash"])
        op.create_index("ix_idempotency_records_created_at", "idempotency_records", ["created_at"])


def downgrade() -> None:
    for table_name in (
        "idempotency_records",
        "execution_leases",
        "operation_memory_entries",
        "business_state_snapshots",
        "autonomous_case_steps",
        "autonomous_cases",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
