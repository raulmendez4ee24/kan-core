"""Add autonomy_v2_benchmarks table for persistent run ranking.

Revision ID: 20260221_0008
Revises: 20260219_0007
Create Date: 2026-02-21 05:40:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260221_0008"
down_revision: Union[str, None] = "20260219_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autonomy_v2_benchmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reliability_index", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "run_id", name="uq_autonomy_v2_benchmarks_org_run"),
    )
    op.create_index(
        op.f("ix_autonomy_v2_benchmarks_organization_id"),
        "autonomy_v2_benchmarks",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_autonomy_v2_benchmarks_run_id"),
        "autonomy_v2_benchmarks",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_autonomy_v2_benchmarks_status"),
        "autonomy_v2_benchmarks",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_autonomy_v2_benchmarks_created_at"),
        "autonomy_v2_benchmarks",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_autonomy_v2_benchmarks_org_reliability_created",
        "autonomy_v2_benchmarks",
        ["organization_id", "reliability_index", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_autonomy_v2_benchmarks_org_reliability_created", table_name="autonomy_v2_benchmarks")
    op.drop_index(op.f("ix_autonomy_v2_benchmarks_created_at"), table_name="autonomy_v2_benchmarks")
    op.drop_index(op.f("ix_autonomy_v2_benchmarks_status"), table_name="autonomy_v2_benchmarks")
    op.drop_index(op.f("ix_autonomy_v2_benchmarks_run_id"), table_name="autonomy_v2_benchmarks")
    op.drop_index(op.f("ix_autonomy_v2_benchmarks_organization_id"), table_name="autonomy_v2_benchmarks")
    op.drop_table("autonomy_v2_benchmarks")
