"""Add integration resilience/observability tables.

Revision ID: 20260217_0002
Revises: 20260217_0001
Create Date: 2026-02-17 00:02:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260217_0002"
down_revision: Union[str, None] = "20260217_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    def _table_exists(table_name: str) -> bool:
        return table_name in sa.inspect(bind).get_table_names()

    def _index_exists(table_name: str, index_name: str) -> bool:
        if not _table_exists(table_name):
            return False
        names = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        return index_name in names

    def _uq_exists(table_name: str, constraint_name: str) -> bool:
        if not _table_exists(table_name):
            return False
        names = {item["name"] for item in sa.inspect(bind).get_unique_constraints(table_name)}
        return constraint_name in names

    if not _table_exists("integration_run_logs"):
        op.create_table(
            "integration_run_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("function_name", sa.String(length=120), nullable=False),
            sa.Column("provider_id", sa.String(length=120), nullable=True),
            sa.Column("integration_name", sa.String(length=120), nullable=True),
            sa.Column("industry", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column(
                "auto_repair_used",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "fallback_used",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("error_code", sa.String(length=120), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_client_id"):
        op.create_index(
            "ix_integration_run_logs_client_id",
            "integration_run_logs",
            ["client_id"],
            unique=False,
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_function_name"):
        op.create_index(
            "ix_integration_run_logs_function_name",
            "integration_run_logs",
            ["function_name"],
            unique=False,
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_provider_id"):
        op.create_index(
            "ix_integration_run_logs_provider_id",
            "integration_run_logs",
            ["provider_id"],
            unique=False,
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_integration_name"):
        op.create_index(
            "ix_integration_run_logs_integration_name",
            "integration_run_logs",
            ["integration_name"],
            unique=False,
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_industry"):
        op.create_index(
            "ix_integration_run_logs_industry",
            "integration_run_logs",
            ["industry"],
            unique=False,
        )
    if not _index_exists("integration_run_logs", "ix_integration_run_logs_status"):
        op.create_index(
            "ix_integration_run_logs_status",
            "integration_run_logs",
            ["status"],
            unique=False,
        )

    if not _table_exists("integration_memory"):
        op.create_table(
            "integration_memory",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("function_name", sa.String(length=120), nullable=False),
            sa.Column("provider_id", sa.String(length=120), nullable=False),
            sa.Column("industry", sa.String(length=120), nullable=True),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("avg_duration_ms", sa.Float(), nullable=True),
            sa.Column("avg_cost_usd", sa.Float(), nullable=True),
            sa.Column("last_status", sa.String(length=64), nullable=True),
            sa.Column("last_error_code", sa.String(length=120), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists("integration_memory", "ix_integration_memory_function_name"):
        op.create_index(
            "ix_integration_memory_function_name",
            "integration_memory",
            ["function_name"],
            unique=False,
        )
    if not _index_exists("integration_memory", "ix_integration_memory_provider_id"):
        op.create_index(
            "ix_integration_memory_provider_id",
            "integration_memory",
            ["provider_id"],
            unique=False,
        )
    if not _index_exists("integration_memory", "ix_integration_memory_industry"):
        op.create_index(
            "ix_integration_memory_industry",
            "integration_memory",
            ["industry"],
            unique=False,
        )
    if not _uq_exists("integration_memory", "uq_integration_memory_function_provider_industry"):
        op.create_unique_constraint(
            "uq_integration_memory_function_provider_industry",
            "integration_memory",
            ["function_name", "provider_id", "industry"],
        )


def downgrade() -> None:
    op.drop_constraint(
        "uq_integration_memory_function_provider_industry",
        "integration_memory",
        type_="unique",
    )
    op.drop_index("ix_integration_memory_industry", table_name="integration_memory")
    op.drop_index("ix_integration_memory_provider_id", table_name="integration_memory")
    op.drop_index("ix_integration_memory_function_name", table_name="integration_memory")
    op.drop_table("integration_memory")

    op.drop_index("ix_integration_run_logs_status", table_name="integration_run_logs")
    op.drop_index("ix_integration_run_logs_industry", table_name="integration_run_logs")
    op.drop_index("ix_integration_run_logs_integration_name", table_name="integration_run_logs")
    op.drop_index("ix_integration_run_logs_provider_id", table_name="integration_run_logs")
    op.drop_index("ix_integration_run_logs_function_name", table_name="integration_run_logs")
    op.drop_index("ix_integration_run_logs_client_id", table_name="integration_run_logs")
    op.drop_table("integration_run_logs")
