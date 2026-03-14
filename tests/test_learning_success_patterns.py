import asyncio
from typing import Any, List

from learning.success_patterns import prioritize_recovery_strategies
from learning.strategy_memory import build_ui_context
from models import GlobalLearning


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def all(self) -> List[Any]:
        if isinstance(self._value, list):
            return self._value
        if self._value is None:
            return []
        return [self._value]


class _ExecuteResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._value)


class DummySession:
    def __init__(self, rows: List[GlobalLearning]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        return _ExecuteResult(self._rows)


def test_prioritize_recovery_strategies_prefers_org_and_ui_match(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_STRATEGY_LEARNING", "true")
    ui_context = build_ui_context(
        action="browser_click",
        args={"selector": "#login", "url": "https://app.example.com"},
        metadata={"industry": "real_estate"},
    )
    signature = str(ui_context.get("ui_signature"))

    low_row = GlobalLearning(
        pattern_key="desktop_strategy:global:sig:search_again",
        industry="real_estate",
        function_name="desktop_recovery:browser_click",
        provider_id="search_again",
        sample_size=15,
        success_rate=0.25,
        avg_impact_score=30.0,
        learning_metadata={
            "scope": "global",
            "ui_signature": "other",
        },
    )
    best_row = GlobalLearning(
        pattern_key="desktop_strategy:org:ui:adjust",
        industry="real_estate",
        function_name="desktop_recovery:browser_click",
        provider_id="adjust_coordinates",
        sample_size=10,
        success_rate=0.9,
        avg_impact_score=88.0,
        learning_metadata={
            "scope": "org:11111111-1111-1111-1111-111111111111",
            "organization_id": "11111111-1111-1111-1111-111111111111",
            "ui_signature": signature,
        },
    )

    ordered = asyncio.run(
        prioritize_recovery_strategies(
            DummySession([low_row, best_row]),
            organization_id="11111111-1111-1111-1111-111111111111",
            industry="real_estate",
            action="browser_click",
            args={"selector": "#login", "url": "https://app.example.com"},
            metadata={"industry": "real_estate"},
            candidate_strategies=["search_again", "adjust_coordinates"],
        )
    )

    assert ordered[0] == "adjust_coordinates"


def test_prioritize_recovery_strategies_returns_candidates_without_memory(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_STRATEGY_LEARNING", "true")
    ordered = asyncio.run(
        prioritize_recovery_strategies(
            DummySession([]),
            organization_id="11111111-1111-1111-1111-111111111111",
            industry="general",
            action="click",
            args={"x": 100, "y": 120},
            metadata={},
            candidate_strategies=["search_again", "wait_and_retry"],
        )
    )
    assert ordered == ["search_again", "wait_and_retry"]
