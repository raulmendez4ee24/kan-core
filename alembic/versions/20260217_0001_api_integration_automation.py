"""Add automation fields to api_integrations.

Revision ID: 20260217_0001
Revises:
Create Date: 2026-02-17 00:01:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260217_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {str(col.get("name")) for col in inspector.get_columns("api_integrations")}

    if "docs_url" not in columns:
        op.add_column("api_integrations", sa.Column("docs_url", sa.String(length=1024), nullable=True))
    if "openapi_url" not in columns:
        op.add_column("api_integrations", sa.Column("openapi_url", sa.String(length=1024), nullable=True))
    if "openapi_spec" not in columns:
        op.add_column(
            "api_integrations",
            sa.Column("openapi_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if "last_workflow_id" not in columns:
        op.add_column(
            "api_integrations",
            sa.Column("last_workflow_id", sa.String(length=128), nullable=True),
        )
    if "last_workflow_url" not in columns:
        op.add_column(
            "api_integrations",
            sa.Column("last_workflow_url", sa.String(length=2048), nullable=True),
        )

    unique_constraints = {
        str(uc.get("name"))
        for uc in inspector.get_unique_constraints("api_integrations")
        if uc.get("name")
    }
    if "uq_api_integrations_client_name" not in unique_constraints:
        op.create_unique_constraint(
            "uq_api_integrations_client_name",
            "api_integrations",
            ["client_id", "name"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    unique_constraints = {
        str(uc.get("name"))
        for uc in inspector.get_unique_constraints("api_integrations")
        if uc.get("name")
    }
    if "uq_api_integrations_client_name" in unique_constraints:
        op.drop_constraint("uq_api_integrations_client_name", "api_integrations", type_="unique")

    columns = {str(col.get("name")) for col in inspector.get_columns("api_integrations")}
    if "last_workflow_url" in columns:
        op.drop_column("api_integrations", "last_workflow_url")
    if "last_workflow_id" in columns:
        op.drop_column("api_integrations", "last_workflow_id")
    if "openapi_spec" in columns:
        op.drop_column("api_integrations", "openapi_spec")
    if "openapi_url" in columns:
        op.drop_column("api_integrations", "openapi_url")
    if "docs_url" in columns:
        op.drop_column("api_integrations", "docs_url")
