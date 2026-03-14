"""Ensure crm_leads.external_id exists for CRM upsert compatibility.

Revision ID: 20260302_0015
Revises: 20260302_0014
Create Date: 2026-03-02 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260302_0015"
down_revision: Union[str, None] = "20260302_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return False
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def _has_unique(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return False
    names = {c["name"] for c in inspector.get_unique_constraints(table_name)}
    return constraint_name in names


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return False
    names = {i["name"] for i in inspector.get_indexes(table_name)}
    return index_name in names


def upgrade() -> None:
    if not _has_table("crm_leads"):
        return

    if not _has_column("crm_leads", "external_id"):
        op.add_column("crm_leads", sa.Column("external_id", sa.String(length=128), nullable=True))

    uq_name = "uq_crm_leads_org_external_id"
    if not _has_unique("crm_leads", uq_name):
        op.create_unique_constraint(uq_name, "crm_leads", ["organization_id", "external_id"])

    ix_name = "ix_crm_leads_external_id"
    if not _has_index("crm_leads", ix_name):
        op.create_index(ix_name, "crm_leads", ["external_id"], unique=False)


def downgrade() -> None:
    # Intentionally no-op: external_id is part of canonical CRM schema and
    # may pre-exist from earlier revisions in existing environments.
    pass

