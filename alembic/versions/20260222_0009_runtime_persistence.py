"""Add persistent runtime state/events/hooks tables.

Revision ID: 20260222_0009
Revises: 20260221_0008
Create Date: 2026-02-22 11:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260222_0009"
down_revision: Union[str, None] = "20260221_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "runtime_runs"):
        op.create_table(
            "runtime_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("run_id", sa.String(length=120), nullable=False),
            sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("session_id", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_runtime_runs_run_id"),
        )
        op.create_index("ix_runtime_runs_run_id", "runtime_runs", ["run_id"], unique=False)
        op.create_index("ix_runtime_runs_client_id", "runtime_runs", ["client_id"], unique=False)
        op.create_index("ix_runtime_runs_session_id", "runtime_runs", ["session_id"], unique=False)
        op.create_index("ix_runtime_runs_status", "runtime_runs", ["status"], unique=False)
        op.create_index("ix_runtime_runs_created_at", "runtime_runs", ["created_at"], unique=False)
        op.create_index("ix_runtime_runs_updated_at", "runtime_runs", ["updated_at"], unique=False)

    if not _table_exists(inspector, "runtime_events"):
        op.create_table(
            "runtime_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("run_id", sa.String(length=120), nullable=False),
            sa.Column("session_id", sa.String(length=160), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "seq", name="uq_runtime_events_run_seq"),
        )
        op.create_index("ix_runtime_events_run_id", "runtime_events", ["run_id"], unique=False)
        op.create_index("ix_runtime_events_session_id", "runtime_events", ["session_id"], unique=False)
        op.create_index("ix_runtime_events_event_type", "runtime_events", ["event_type"], unique=False)
        op.create_index("ix_runtime_events_created_at", "runtime_events", ["created_at"], unique=False)

    if not _table_exists(inspector, "runtime_hook_registrations"):
        op.create_table(
            "runtime_hook_registrations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("event", sa.String(length=80), nullable=False),
            sa.Column("mode", sa.String(length=24), nullable=False, server_default="annotate"),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_runtime_hook_registrations_organization_id",
            "runtime_hook_registrations",
            ["organization_id"],
            unique=False,
        )
        op.create_index("ix_runtime_hook_registrations_event", "runtime_hook_registrations", ["event"], unique=False)
        op.create_index(
            "ix_runtime_hook_registrations_version",
            "runtime_hook_registrations",
            ["version"],
            unique=False,
        )
        op.create_index(
            "ix_runtime_hook_registrations_created_at",
            "runtime_hook_registrations",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "runtime_hook_registrations" in tables:
        op.drop_index("ix_runtime_hook_registrations_created_at", table_name="runtime_hook_registrations")
        op.drop_index("ix_runtime_hook_registrations_version", table_name="runtime_hook_registrations")
        op.drop_index("ix_runtime_hook_registrations_event", table_name="runtime_hook_registrations")
        op.drop_index("ix_runtime_hook_registrations_organization_id", table_name="runtime_hook_registrations")
        op.drop_table("runtime_hook_registrations")

    if "runtime_events" in tables:
        op.drop_index("ix_runtime_events_created_at", table_name="runtime_events")
        op.drop_index("ix_runtime_events_event_type", table_name="runtime_events")
        op.drop_index("ix_runtime_events_session_id", table_name="runtime_events")
        op.drop_index("ix_runtime_events_run_id", table_name="runtime_events")
        op.drop_table("runtime_events")

    if "runtime_runs" in tables:
        op.drop_index("ix_runtime_runs_updated_at", table_name="runtime_runs")
        op.drop_index("ix_runtime_runs_created_at", table_name="runtime_runs")
        op.drop_index("ix_runtime_runs_status", table_name="runtime_runs")
        op.drop_index("ix_runtime_runs_session_id", table_name="runtime_runs")
        op.drop_index("ix_runtime_runs_client_id", table_name="runtime_runs")
        op.drop_index("ix_runtime_runs_run_id", table_name="runtime_runs")
        op.drop_table("runtime_runs")
