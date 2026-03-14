import asyncio
from typing import Any, List, Optional
from uuid import UUID, uuid4

from learning.reinforcement_optimizer import calibrate_org_learning_policy
from models import GlobalLearning


class _Scalar:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def all(self) -> List[Any]:
        if isinstance(self._value, list):
            return self._value
        if self._value is None:
            return []
        return [self._value]


class _Execute:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _Scalar:
        return _Scalar(self._value)

    def first(self) -> Any:
        return self._value


class _OrgPolicyRow:
    def __init__(self, org_id: UUID) -> None:
        self.id = uuid4()
        self.organization_id = org_id
        self.policy: dict = {}
        self.updated_by_user_id: Optional[UUID] = None
        self.created_at = None
        self.updated_at = None


class DummySession:
    def __init__(self, results: List[Any]) -> None:
        self._results = list(results)

    async def execute(self, _stmt: Any) -> _Execute:
        if not self._results:
            return _Execute(None)
        return _Execute(self._results.pop(0))

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None


def test_calibrate_org_learning_policy_noop_when_policies_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_POLICIES", "false")
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    out = asyncio.run(calibrate_org_learning_policy(DummySession([]), organization_id=org_id))
    assert out["updated"] is False


def test_calibrate_org_learning_policy_writes_learning(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_ENTERPRISE_POLICIES", "true")

    # Prevent backfill helper from consuming execute() slots.
    async def _noop_refresh(*_args: Any, **_kwargs: Any) -> Any:
        return {"updated": 0}

    monkeypatch.setattr("learning.reinforcement_optimizer.refresh_tool_learning_from_logs", _noop_refresh)

    org_id = UUID("11111111-1111-1111-1111-111111111111")
    org_row = _OrgPolicyRow(org_id)

    api_tool = GlobalLearning(
        pattern_key="tool_choice:org:11111111-1111-1111-1111-111111111111:api",
        industry="general",
        function_name="tool_choice",
        provider_id="api",
        sample_size=40,
        success_rate=0.9,
        avg_impact_score=85.0,
        learning_metadata={"scope": "org:11111111-1111-1111-1111-111111111111", "organization_id": str(org_id)},
    )
    desktop_tool = GlobalLearning(
        pattern_key="tool_choice:org:11111111-1111-1111-1111-111111111111:desktop",
        industry="general",
        function_name="tool_choice",
        provider_id="desktop",
        sample_size=10,
        success_rate=0.4,
        avg_impact_score=40.0,
        learning_metadata={"scope": "org:11111111-1111-1111-1111-111111111111", "organization_id": str(org_id)},
    )

    # execute() call order inside calibrate_org_learning_policy:
    # 1) select OrgPolicy -> org_row
    # 2) select GlobalLearning tool_choice -> [api_tool, desktop_tool]
    # 3) api_stmt aggregate -> tuple
    session = DummySession([org_row, [api_tool, desktop_tool], (None, 0.8, 50)])

    out = asyncio.run(calibrate_org_learning_policy(session, organization_id=org_id))
    assert out["updated"] is True
    assert "learning" in out
    assert "tool_weights" in out["learning"]
    assert "exploration_epsilon" in out["learning"]
