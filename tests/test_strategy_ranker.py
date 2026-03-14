import asyncio
from typing import Any, List

from learning.strategy_memory import build_ui_context
from learning.strategy_ranker import rank_recovery_strategies, rank_tools
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
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        return _ExecuteResult(self._rows)


def test_rank_recovery_strategies_prefers_high_success(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_STRATEGY_LEARNING", "true")
    monkeypatch.setenv("LEARNING_EXPLORATION_EPSILON", "0")

    ui_context = build_ui_context(
        action="browser_click",
        args={"selector": "#login", "url": "https://app.example.com"},
        metadata={"industry": "real_estate"},
    )
    signature = str(ui_context.get("ui_signature"))

    best_row = GlobalLearning(
        pattern_key="desktop_strategy:org:sig:adjust_coordinates",
        industry="real_estate",
        function_name="desktop_recovery:browser_click",
        provider_id="adjust_coordinates",
        sample_size=25,
        success_rate=0.92,
        avg_impact_score=92.0,
        learning_metadata={
            "scope": "org:11111111-1111-1111-1111-111111111111",
            "organization_id": "11111111-1111-1111-1111-111111111111",
            "ui_signature": signature,
            "recent_outcomes": [{"success": True}],
        },
    )
    low_row = GlobalLearning(
        pattern_key="desktop_strategy:org:sig:search_again",
        industry="real_estate",
        function_name="desktop_recovery:browser_click",
        provider_id="search_again",
        sample_size=30,
        success_rate=0.25,
        avg_impact_score=30.0,
        learning_metadata={
            "scope": "org:11111111-1111-1111-1111-111111111111",
            "organization_id": "11111111-1111-1111-1111-111111111111",
            "ui_signature": signature,
            "recent_outcomes": [{"success": False}, {"success": False}],
        },
    )

    ordered = asyncio.run(
        rank_recovery_strategies(
            DummySession([best_row, low_row]),
            organization_id="11111111-1111-1111-1111-111111111111",
            industry="real_estate",
            action="browser_click",
            args={"selector": "#login", "url": "https://app.example.com"},
            metadata={"industry": "real_estate"},
            candidates=["search_again", "adjust_coordinates"],
        )
    )

    assert ordered[0] == "adjust_coordinates"


def test_rank_tools_applies_learning_bias(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_STRATEGY_LEARNING", "true")
    monkeypatch.setenv("LEARNING_EXPLORATION_EPSILON", "0")

    api_row = GlobalLearning(
        pattern_key="tool_choice:org:11111111-1111-1111-1111-111111111111:api",
        industry="general",
        function_name="tool_choice",
        provider_id="api",
        sample_size=40,
        success_rate=0.9,
        avg_impact_score=85.0,
        learning_metadata={
            "scope": "org:11111111-1111-1111-1111-111111111111",
            "organization_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    desktop_row = GlobalLearning(
        pattern_key="tool_choice:org:11111111-1111-1111-1111-111111111111:desktop",
        industry="general",
        function_name="tool_choice",
        provider_id="desktop",
        sample_size=40,
        success_rate=0.2,
        avg_impact_score=20.0,
        learning_metadata={
            "scope": "org:11111111-1111-1111-1111-111111111111",
            "organization_id": "11111111-1111-1111-1111-111111111111",
        },
    )

    ranked = asyncio.run(
        rank_tools(
            DummySession([api_row, desktop_row]),
            organization_id="11111111-1111-1111-1111-111111111111",
            goal_context={"max_cost": 1.0, "exploration_epsilon": 0.0},
            candidates=[
                {"tool": "desktop", "available": True, "confidence": 0.6, "health": 0.6, "compatibility": 0.7, "speed": 0.6, "history": 0.0, "cost": 0.0},
                {"tool": "api", "available": True, "confidence": 0.6, "health": 0.6, "compatibility": 0.7, "speed": 0.6, "history": 0.0, "cost": 0.2},
            ],
        )
    )

    assert ranked[0]["tool"] == "api"
    assert "learned_score" in ranked[0]

