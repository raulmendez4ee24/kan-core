from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from tools import db_migrate


def _load_revision_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "20260224_0010_objective_runs_client_id.py"
    spec = importlib.util.spec_from_file_location("rev_20260224_0010", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_integration_memory_revision_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "20260301_0013_integration_memory_client_id.py"
    spec = importlib.util.spec_from_file_location("rev_20260301_0013", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_objective_runs_client_id_migration_adds_column_and_index(monkeypatch):
    revision = _load_revision_module()
    calls = {"add_column": 0, "create_index": 0}

    class _FakeInspector:
        def get_table_names(self):
            return ["objective_runs"]

        def get_columns(self, _table_name):
            return [{"name": "id"}, {"name": "objective_id"}]

        def get_indexes(self, _table_name):
            return []

    class _FakeOp:
        def get_bind(self):
            return object()

        def add_column(self, table_name, column):
            _ = column
            assert table_name == "objective_runs"
            calls["add_column"] += 1

        def create_index(self, index_name, table_name, columns, unique=False):
            assert index_name == "idx_objective_runs_client_id"
            assert table_name == "objective_runs"
            assert columns == ["client_id"]
            assert unique is False
            calls["create_index"] += 1

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(revision.sa, "inspect", lambda _bind: _FakeInspector())

    revision.upgrade()

    assert calls["add_column"] == 1
    assert calls["create_index"] == 1


def test_objective_runs_client_id_migration_is_noop_when_column_exists(monkeypatch):
    revision = _load_revision_module()
    calls = {"add_column": 0, "create_index": 0}

    class _FakeInspector:
        def get_table_names(self):
            return ["objective_runs"]

        def get_columns(self, _table_name):
            return [{"name": "id"}, {"name": "client_id"}]

        def get_indexes(self, _table_name):
            return [{"name": "idx_objective_runs_client_id"}]

    class _FakeOp:
        def get_bind(self):
            return object()

        def add_column(self, _table_name, _column):
            calls["add_column"] += 1

        def create_index(self, _index_name, _table_name, _columns, unique=False):
            _ = unique
            calls["create_index"] += 1

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(revision.sa, "inspect", lambda _bind: _FakeInspector())

    revision.upgrade()

    assert calls["add_column"] == 0
    assert calls["create_index"] == 0


def test_db_migrate_runs_alembic_upgrade_head(monkeypatch):
    called = {}

    def _fake_upgrade(config, revision):
        called["config"] = config
        called["revision"] = revision

    monkeypatch.setattr(db_migrate.command, "upgrade", _fake_upgrade)

    db_migrate.run_upgrade("head")

    assert called["revision"] == "head"
    assert called["config"].config_file_name.endswith("alembic.ini")
    assert isinstance(called["config"], Config)


def test_integration_memory_client_id_migration_updates_schema(monkeypatch):
    revision = _load_integration_memory_revision_module()
    calls = {"add_column": 0, "create_index": 0, "drop_constraint": 0, "create_unique_constraint": 0}

    class _FakeInspector:
        def get_table_names(self):
            return ["integration_memory"]

        def get_columns(self, _table_name):
            return [{"name": "id"}, {"name": "function_name"}]

        def get_indexes(self, _table_name):
            return []

        def get_unique_constraints(self, _table_name):
            return [{"name": "uq_integration_memory_function_provider_industry"}]

    class _FakeOp:
        def get_bind(self):
            return object()

        def add_column(self, table_name, column):
            _ = column
            assert table_name == "integration_memory"
            calls["add_column"] += 1

        def create_index(self, index_name, table_name, columns, unique=False):
            assert index_name == "ix_integration_memory_client_id"
            assert table_name == "integration_memory"
            assert columns == ["client_id"]
            assert unique is False
            calls["create_index"] += 1

        def drop_constraint(self, name, table_name, type_="unique"):
            assert name == "uq_integration_memory_function_provider_industry"
            assert table_name == "integration_memory"
            assert type_ == "unique"
            calls["drop_constraint"] += 1

        def create_unique_constraint(self, name, table_name, columns):
            assert name == "uq_integration_memory_client_function_provider_industry"
            assert table_name == "integration_memory"
            assert columns == ["client_id", "function_name", "provider_id", "industry"]
            calls["create_unique_constraint"] += 1

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(revision.sa, "inspect", lambda _bind: _FakeInspector())

    revision.upgrade()

    assert calls["add_column"] == 1
    assert calls["create_index"] == 1
    assert calls["drop_constraint"] == 1
    assert calls["create_unique_constraint"] == 1
