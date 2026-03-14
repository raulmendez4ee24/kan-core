import asyncio

import pytest

from recovery.retry_engine import RetryEngine, ScreenshotPayload
from recovery.schemas import ErrorAnalysisResult


def _screen(width: int = 1280, height: int = 720) -> ScreenshotPayload:
    return ScreenshotPayload(
        image_bytes=b"fake-image",
        width=width,
        height=height,
        metadata={},
    )


def test_retry_engine_element_not_found_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, _args: dict, _meta: dict) -> dict:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"status": "failed", "command_id": "cmd-r1"}
        return {"status": "success", "command_id": "cmd-r2"}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="element_not_found",
            reason="Element not found",
            suggested_fix="search_again",
            confidence=0.8,
        )

    monkeypatch.setattr(
        "recovery.retry_engine.analyze_screen",
        lambda *_args, **_kwargs: {
            "element": "login_button",
            "x": 540,
            "y": 320,
            "confidence": 0.9,
        },
    )

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "search_again",
            max_attempts=3,
            original_action="click",
            original_args={"button": "left", "clicks": 1},
            instruction="Find login button",
            expected_outcome="Login button clicked",
        )
    )

    assert result.success is True
    assert result.attempts_used == 2
    assert result.logs[0].error_type == "element_not_found"


def test_retry_engine_adjusts_coordinates_when_incorrect(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_args = []

    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, args: dict, _meta: dict) -> dict:
        sent_args.append(dict(args))
        return {"status": "failed", "command_id": "cmd-coords"}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="incorrect_coordinates",
            reason="Coordinates are off",
            suggested_fix="adjust_coordinates",
            confidence=0.7,
        )

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "adjust_coordinates",
            max_attempts=1,
            original_action="click",
            original_args={"x": 100, "y": 200, "button": "left", "clicks": 1},
            instruction="Click login",
            expected_outcome="Login clicked",
        )
    )

    assert result.success is False
    assert sent_args and sent_args[0]["x"] == 118
    assert sent_args[0]["y"] == 212


def test_retry_engine_fails_after_max_attempts() -> None:
    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, _args: dict, _meta: dict) -> dict:
        return {"status": "failed", "command_id": "cmd-failed"}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="unknown",
            reason="Still failing",
            suggested_fix="wait_and_retry",
            confidence=0.3,
        )

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "wait_and_retry",
            max_attempts=2,
            original_action="click",
            original_args={"button": "left", "clicks": 1},
            instruction="Click login",
            expected_outcome="Login clicked",
        )
    )

    assert result.success is False
    assert result.attempts_used == 2
    assert len(result.logs) == 2


def test_retry_engine_uses_alternate_selector_strategy() -> None:
    seen_selectors = []

    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, args: dict, _meta: dict) -> dict:
        seen_selectors.append(args.get("selector"))
        if args.get("selector") == "#secondary":
            return {"status": "success", "command_id": "cmd-alt-ok"}
        return {"status": "failed", "command_id": "cmd-alt-fail"}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="element_not_found",
            reason="Primary selector failed",
            suggested_fix="retry_with_alternate_selector",
            confidence=0.8,
        )

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "retry_with_alternate_selector",
            max_attempts=2,
            original_action="browser_click",
            original_args={
                "selector": "#primary",
                "alt_selectors": ["#secondary", "#tertiary"],
            },
            instruction="Click login button",
            expected_outcome="Login click success",
        )
    )

    assert result.success is True
    assert seen_selectors[0] == "#secondary"


def test_retry_engine_prioritizes_memory_strategy_after_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used_strategies = []
    attempts = {"count": 0}

    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, _args: dict, meta: dict) -> dict:
        attempts["count"] += 1
        used_strategies.append(str(meta.get("recovery_strategy")))
        if attempts["count"] == 1:
            return {"status": "failed", "command_id": "cmd-pref-1"}
        return {"status": "success", "command_id": "cmd-pref-2"}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="element_not_found",
            reason="Still not found",
            suggested_fix="search_again",
            confidence=0.6,
        )

    monkeypatch.setattr(
        "recovery.retry_engine.analyze_screen",
        lambda *_args, **_kwargs: {"element": "target", "x": 100, "y": 120, "confidence": 0.7},
    )

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "search_again",
            max_attempts=2,
            original_action="click",
            original_args={"x": 80, "y": 90},
            instruction="Find target",
            expected_outcome="Target clicked",
            strategy_preferences=["adjust_coordinates", "wait_and_retry"],
        )
    )

    assert result.success is True
    assert used_strategies[0] == "search_again"
    assert used_strategies[1] == "adjust_coordinates"


def test_retry_engine_emits_learning_events_callback() -> None:
    events = []
    attempts = {"count": 0}

    async def fake_take_screenshot() -> ScreenshotPayload:
        return _screen()

    async def fake_execute_action(_action: str, _args: dict, _meta: dict) -> dict:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"status": "failed", "command_id": "cmd-evt-1", "result_duration_ms": 240}
        return {"status": "success", "command_id": "cmd-evt-2", "result_duration_ms": 110}

    def fake_detect_error(*_args, **_kwargs) -> ErrorAnalysisResult:
        return ErrorAnalysisResult(
            success=False,
            error_type="wrong_screen",
            reason="Wrong page",
            suggested_fix="wait_and_retry",
            confidence=0.4,
        )

    async def record_event(payload: dict) -> None:
        events.append(dict(payload))

    engine = RetryEngine(
        take_screenshot=fake_take_screenshot,
        execute_action=fake_execute_action,
        detect_error_fn=fake_detect_error,
    )

    result = asyncio.run(
        engine.retry_action(
            "cmd-0",
            "wait_and_retry",
            max_attempts=2,
            original_action="click",
            original_args={"button": "left"},
            instruction="Click submit",
            expected_outcome="Submit clicked",
            record_attempt_fn=record_event,
        )
    )

    assert result.success is True
    assert len(events) == 2
    assert events[0]["success"] is False
    assert events[1]["success"] is True
