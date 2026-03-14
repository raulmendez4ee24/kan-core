"""Scope integration_memory rows by client.

Revision ID: 20260301_0013
Revises: 20260227_0012
Create Date: 2026-03-01 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260301_0013"
down_revision: Union[str, None] = "20260227_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "integration_memory"
_COLUMN_NAME = "client_id"
_INDEX_NAME = "ix_integration_memory_client_id"
_OLD_UNIQUE = "uq_integration_memory_function_provider_industry"
_NEW_UNIQUE = "uq_integration_memory_client_function_provider_industry"


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


def _unique_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uniques = {str(item.get("name") or "") for item in inspector.get_unique_constraints(table_name)}
    indexes = {str(idx.get("name") or "") for idx in inspector.get_indexes(table_name) if idx.get("unique")}
    return uniques | indexes


def _unique_constraint_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {str(item.get("name") or "") for item in inspector.get_unique_constraints(table_name)}


def _unique_index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {str(idx.get("name") or "") for idx in inspector.get_indexes(table_name) if idx.get("unique")}


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

    unique_constraints = _unique_constraint_names(_TABLE_NAME)
    unique_indexes = _unique_index_names(_TABLE_NAME)
    if _OLD_UNIQUE in unique_constraints:
        op.drop_constraint(_OLD_UNIQUE, _TABLE_NAME, type_="unique")
    elif _OLD_UNIQUE in unique_indexes:
        op.drop_index(_OLD_UNIQUE, table_name=_TABLE_NAME)
    if _NEW_UNIQUE not in _unique_names(_TABLE_NAME):
        op.create_unique_constraint(
            _NEW_UNIQUE,
            _TABLE_NAME,
            [_COLUMN_NAME, "function_name", "provider_id", "industry"],
        )


def downgrade() -> None:
    if not _has_table(_TABLE_NAME):
        return

    unique_names = _unique_names(_TABLE_NAME)
    if _NEW_UNIQUE in unique_names:
        op.drop_constraint(_NEW_UNIQUE, _TABLE_NAME, type_="unique")
    if _OLD_UNIQUE not in _unique_names(_TABLE_NAME):
        op.create_unique_constraint(
            _OLD_UNIQUE,
            _TABLE_NAME,
            ["function_name", "provider_id", "industry"],
        )
    if _INDEX_NAME in _index_names(_TABLE_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE_NAME)
    if _COLUMN_NAME in _column_names(_TABLE_NAME):
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
