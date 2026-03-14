"""Ensure objective_runs.client_id exists.

Revision ID: 20260224_0010
Revises: 20260222_0009
Create Date: 2026-02-24 11:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260224_0010"
down_revision: Union[str, None] = "20260222_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "objective_runs"
_COLUMN_NAME = "client_id"
_INDEX_NAME = "idx_objective_runs_client_id"


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {str(col.get("name") or "") for col in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {str(idx.get("name") or "") for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_table(_TABLE_NAME):
        return
    if _COLUMN_NAME not in _column_names(_TABLE_NAME):
        op.add_column(
            _TABLE_NAME,
            sa.Column(_COLUMN_NAME, postgresql.UUID(as_uuid=True), nullable=True),
        )
    if _INDEX_NAME not in _index_names(_TABLE_NAME):
        op.create_index(_INDEX_NAME, _TABLE_NAME, [_COLUMN_NAME], unique=False)


def downgrade() -> None:
    if not _has_table(_TABLE_NAME):
        return
    if _INDEX_NAME in _index_names(_TABLE_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE_NAME)
    if _COLUMN_NAME in _column_names(_TABLE_NAME):
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
