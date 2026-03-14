from schemas.action_contract import ActionContract

from execution.desktop_executor import is_desktop_action, send_to_desktop_agent


def _action(*, action_type: str = "integration", integration: str = "shopify") -> ActionContract:
    return ActionContract(
        contract_version="1.0",
        action_type=action_type,  # type: ignore[arg-type]
        action="lookup_catalog",
        integration=integration,
        arguments={},
        rationale="test",
    )


def test_is_desktop_action_true_by_action_type() -> None:
    assert is_desktop_action(_action(action_type="desktop")) is True


def test_is_desktop_action_true_by_integration() -> None:
    assert is_desktop_action(_action(integration="desktop")) is True


def test_is_desktop_action_true_by_browser_integration() -> None:
    assert is_desktop_action(_action(integration="browser")) is True


def test_is_desktop_action_false_when_not_desktop() -> None:
    assert is_desktop_action(_action(action_type="integration", integration="shopify")) is False


def test_send_to_desktop_agent_feature_disabled_rejects(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "false")

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="open_program",
            parameters={"name": "notepad"},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "feature_disabled"


def test_send_to_desktop_agent_invalid_action_rejects(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="do_anything",
            parameters={},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "unsupported_action"


def test_send_to_desktop_agent_invalid_click_args_rejects(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="click",
            parameters={"x": 100},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "invalid_parameters"


def test_send_to_desktop_agent_browser_rejects_when_approval_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="browser_goto",
            parameters={"url": "https://app.example.com"},
            metadata={"approval": {"required": False}},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "browser_requires_human_approval"


def test_send_to_desktop_agent_browser_rejects_non_allowlisted_domain(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="browser_goto",
            parameters={"url": "https://evil.com"},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "invalid_parameters"


class _DummySession:
    def add(self, _obj) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        from datetime import datetime, timezone
        from uuid import uuid4

        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        now = datetime.now(timezone.utc)
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        obj.updated_at = now


class _DummySessionCtx:
    def __init__(self) -> None:
        self._session = _DummySession()

    async def __aenter__(self) -> _DummySession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def test_send_to_desktop_agent_auto_approves_low_risk_when_enabled(monkeypatch) -> None:
    async def fake_find_agent_id(**_kwargs):
        from uuid import UUID

        return UUID("22222222-2222-2222-2222-222222222222")

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("ENABLE_DESKTOP_AUTO_APPROVAL", "true")
    monkeypatch.setenv("DESKTOP_AUTO_APPROVAL_MAX_RISK", "low")
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')
    monkeypatch.setattr("execution.desktop_executor._find_agent_id", fake_find_agent_id)
    monkeypatch.setattr("execution.desktop_executor.write_audit_log", fake_audit)
    monkeypatch.setattr("execution.desktop_executor.AsyncSessionLocal", lambda: _DummySessionCtx())

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="browser_goto",
            parameters={"url": "https://app.example.com"},
            metadata={"source": "desktop_autonomy"},
        )
    )

    assert result["accepted"] is True
    assert result["status"] == "approved"
    assert result["approval_required"] is False
    assert result["risk_level"] == "low"


def test_send_to_desktop_agent_keeps_high_risk_pending_with_low_auto_approval(monkeypatch) -> None:
    async def fake_find_agent_id(**_kwargs):
        from uuid import UUID

        return UUID("22222222-2222-2222-2222-222222222222")

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("ENABLE_DESKTOP_AUTO_APPROVAL", "true")
    monkeypatch.setenv("DESKTOP_AUTO_APPROVAL_MAX_RISK", "low")
    monkeypatch.setattr("execution.desktop_executor._find_agent_id", fake_find_agent_id)
    monkeypatch.setattr("execution.desktop_executor.write_audit_log", fake_audit)
    monkeypatch.setattr("execution.desktop_executor.AsyncSessionLocal", lambda: _DummySessionCtx())

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="click",
            parameters={"x": 10, "y": 20},
            metadata={"source": "desktop_autonomy"},
        )
    )

    assert result["accepted"] is True
    assert result["status"] == "pending_approval"
    assert result["approval_required"] is True
    assert result["risk_level"] == "high"


def test_send_to_desktop_agent_remote_mode_returns_runner_offline(monkeypatch) -> None:
    async def fake_find_agent_id(**_kwargs):
        return None

    monkeypatch.setenv("ENABLE_DESKTOP_AGENT", "true")
    monkeypatch.setenv("RUNNER_MODE", "remote")
    monkeypatch.setattr("execution.desktop_executor._find_agent_id", fake_find_agent_id)

    result = _run_async(
        send_to_desktop_agent(
            client_id="11111111-1111-1111-1111-111111111111",
            action="open_program",
            parameters={"name": "Google Chrome"},
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "runner_offline"


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)
