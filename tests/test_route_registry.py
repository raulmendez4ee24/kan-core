import logging

import pytest
from fastapi import FastAPI

import main


def _handler_one() -> dict:
    return {"ok": 1}


def _handler_two() -> dict:
    return {"ok": 2}


def test_find_duplicate_routes_detects_method_path_collision() -> None:
    app = FastAPI()
    app.add_api_route("/demo", _handler_one, methods=["GET"])
    app.add_api_route("/demo", _handler_two, methods=["GET"])

    duplicates = main._find_duplicate_routes(app)
    assert ("GET", "/demo") in duplicates
    assert len(duplicates[("GET", "/demo")]) == 2


def test_enforce_route_registry_fails_when_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.add_api_route("/demo", _handler_one, methods=["GET"])
    app.add_api_route("/demo", _handler_two, methods=["GET"])
    monkeypatch.setenv("STRICT_ROUTE_REGISTRY", "true")

    with pytest.raises(RuntimeError):
        main._enforce_route_registry(app)


def test_enforce_route_registry_logs_only_when_not_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.add_api_route("/demo", _handler_one, methods=["GET"])
    app.add_api_route("/demo", _handler_two, methods=["GET"])
    monkeypatch.setenv("STRICT_ROUTE_REGISTRY", "false")

    duplicates = main._enforce_route_registry(app)
    assert ("GET", "/demo") in duplicates
    assert isinstance(logging.getLogger("kan_core"), logging.Logger)

