"""Add converted_at to crm_leads for conversion tracking.

Revision ID: 20260319_0016
Revises: 20260302_0015
Create Date: 2026-03-19 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260319_0016"
down_revision: Union[str, None] = "20260302_0015"
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


def upgrade() -> None:
    if not _has_table("crm_leads"):
        return
    if not _has_column("crm_leads", "converted_at"):
        op.add_column(
            "crm_leads",
            sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_crm_leads_converted_at", "crm_leads", ["converted_at"])


def downgrade() -> None:
    pass
