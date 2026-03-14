import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from recovery.schemas import ErrorAnalysisResult, RecoveryLog, RetryResult, RecoveryStrategy
from vision.screen_analyzer import analyze_screen


@dataclass
class ScreenshotPayload:
    image_bytes: bytes
    width: int
    height: int
    metadata: Dict[str, Any]


class RetryEngine:
    _ALLOWED_STRATEGIES: List[RecoveryStrategy] = [
        "adjust_coordinates",
        "scroll_and_retry",
        "search_again",
        "wait_and_retry",
        "fallback_click_center",
        "retry_with_alternate_selector",
    ]

    def __init__(
        self,
        *,
        take_screenshot: Callable[[], Awaitable[ScreenshotPayload]],
        execute_action: Callable[[str, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]],
        detect_error_fn: Callable[[bytes, bytes, str, str], ErrorAnalysisResult],
    ) -> None:
        self._take_screenshot = take_screenshot
        self._execute_action = execute_action
        self._detect_error_fn = detect_error_fn

    def _normalize_strategy(self, strategy: str) -> RecoveryStrategy:
        token = str(strategy or "").strip()
        if token in self._ALLOWED_STRATEGIES:
            return token  # type: ignore[return-value]
        return "search_again"

    def _normalize_strategy_list(self, values: Sequence[str]) -> List[RecoveryStrategy]:
        ordered: List[RecoveryStrategy] = []
        for item in values:
            strategy = self._normalize_strategy(item)
            if strategy not in ordered:
                ordered.append(strategy)
        return ordered

    def _pick_next_strategy(
        self,
        *,
        suggested: RecoveryStrategy,
        preferred: List[RecoveryStrategy],
        used: List[RecoveryStrategy],
    ) -> RecoveryStrategy:
        merged = self._normalize_strategy_list([suggested, *preferred, *self._ALLOWED_STRATEGIES])
        for candidate in merged:
            if candidate not in used:
                return candidate
        return suggested

    def _apply_strategy(
        self,
        *,
        strategy: RecoveryStrategy,
        original_action: str,
        args: Dict[str, Any],
        screenshot: ScreenshotPayload,
        instruction: str,
    ) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        current = dict(args)
        strategy_meta: Dict[str, Any] = {"strategy": strategy}

        if strategy == "adjust_coordinates":
            if "x" in current and isinstance(current.get("x"), int):
                current["x"] = max(0, int(current["x"]) + 18)
            if "y" in current and isinstance(current.get("y"), int):
                current["y"] = max(0, int(current["y"]) + 12)
            return original_action, current, strategy_meta

        if strategy == "wait_and_retry":
            strategy_meta["wait_seconds"] = 1.2
            return original_action, current, strategy_meta

        if strategy == "scroll_and_retry":
            if original_action.startswith("browser_"):
                strategy_meta["pre_action"] = {
                    "action": "browser_press",
                    "args": {"selector": "body", "key": "PageDown"},
                }
            else:
                strategy_meta["pre_action"] = {"action": "press_key", "args": {"key": "pagedown"}}
            return original_action, current, strategy_meta

        if strategy == "fallback_click_center":
            center_x = max(0, int(screenshot.width / 2))
            center_y = max(0, int(screenshot.height / 2))
            if original_action.startswith("browser_"):
                return (
                    "browser_click",
                    {
                        "x": center_x,
                        "y": center_y,
                    },
                    strategy_meta,
                )
            return (
                "click",
                {
                    "x": center_x,
                    "y": center_y,
                    "button": str(current.get("button", "left")),
                    "clicks": int(current.get("clicks", 1)),
                },
                strategy_meta,
            )

        if strategy == "retry_with_alternate_selector":
            alternatives = current.get("alt_selectors")
            if isinstance(alternatives, list):
                index = int(current.get("_recovery_selector_index") or 0)
                if 0 <= index < len(alternatives):
                    selected = str(alternatives[index]).strip()
                    if selected:
                        current["selector"] = selected
                        current["_recovery_selector_index"] = index + 1
                        strategy_meta["selected_alternate_selector"] = selected
                        return original_action, current, strategy_meta

        # search_again
        analysis = analyze_screen(
            screenshot.image_bytes,
            instruction,
            image_mime_type="image/png",
        )
        if original_action.startswith("browser_"):
            return (
                "browser_click",
                {
                    "x": int(analysis["x"]),
                    "y": int(analysis["y"]),
                },
                {
                    **strategy_meta,
                    "detected_element": analysis.get("element"),
                    "vision_confidence": analysis.get("confidence"),
                },
            )
        return (
            "click" if original_action in {"click", "mover_mouse"} else original_action,
            {
                **current,
                "x": int(analysis["x"]),
                "y": int(analysis["y"]),
            },
            {
                **strategy_meta,
                "detected_element": analysis.get("element"),
                "vision_confidence": analysis.get("confidence"),
            },
        )

    async def retry_action(
        self,
        command_id: str,
        strategy: str,
        max_attempts: int = 3,
        *,
        original_action: str,
        original_args: Dict[str, Any],
        instruction: str,
        expected_outcome: str,
        strategy_preferences: Optional[Sequence[str]] = None,
        record_attempt_fn: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> RetryResult:
        max_safe_attempts = max(1, min(int(max_attempts), 10))
        preferred_strategies = self._normalize_strategy_list(strategy_preferences or [])
        current_strategy: RecoveryStrategy = self._normalize_strategy(strategy)
        used_strategies: List[RecoveryStrategy] = []

        logs: list[RecoveryLog] = []
        before_screen = await self._take_screenshot()
        args = dict(original_args)
        last_reason: Optional[str] = None

        for attempt in range(1, max_safe_attempts + 1):
            try:
                action_to_run, args_to_run, strategy_meta = self._apply_strategy(
                    strategy=current_strategy,
                    original_action=original_action,
                    args=args,
                    screenshot=before_screen,
                    instruction=instruction,
                )
            except Exception as exc:
                logs.append(
                    RecoveryLog(
                        attempt=attempt,
                        strategy=current_strategy,
                        status="failed",
                        reason=f"strategy_application_failed: {exc!s}",
                    )
                )
                if record_attempt_fn:
                    try:
                        await record_attempt_fn(
                            {
                                "attempt": attempt,
                                "strategy": current_strategy,
                                "action": original_action,
                                "args": dict(args),
                                "success": False,
                                "duration_ms": None,
                                "error_type": "unknown",
                                "reason": f"strategy_application_failed: {exc!s}",
                                "command_id": None,
                                "command_result_status": "failed",
                            }
                        )
                    except Exception:
                        pass
                last_reason = f"strategy_application_failed: {exc!s}"
                used_strategies.append(current_strategy)
                current_strategy = "wait_and_retry"
                continue

            if current_strategy == "wait_and_retry":
                await asyncio.sleep(1.2)

            pre_action = strategy_meta.get("pre_action")
            if isinstance(pre_action, dict):
                await self._execute_action(
                    str(pre_action.get("action") or "press_key"),
                    dict(pre_action.get("args") or {}),
                    {
                        "recovery_control": "managed",
                        "recovery_origin_command_id": command_id,
                        "recovery_pre_action": True,
                        "recovery_attempt": attempt,
                    },
                )

            result = await self._execute_action(
                action_to_run,
                args_to_run,
                {
                    "recovery_control": "managed",
                    "recovery_origin_command_id": command_id,
                    "recovery_attempt": attempt,
                    "recovery_strategy": current_strategy,
                },
            )

            command_status = str(result.get("status") or "failed")
            created_command_id = (
                str(result.get("command_id")) if result.get("command_id") else None
            )
            duration_ms = result.get("result_duration_ms")
            if command_status == "success":
                logs.append(
                    RecoveryLog(
                        attempt=attempt,
                        strategy=current_strategy,
                        command_id=created_command_id,
                        status="success",
                        command_result_status=command_status,
                        metadata=strategy_meta,
                    )
                )
                if record_attempt_fn:
                    try:
                        await record_attempt_fn(
                            {
                                "attempt": attempt,
                                "strategy": current_strategy,
                                "action": action_to_run,
                                "args": dict(args_to_run),
                                "success": True,
                                "duration_ms": duration_ms,
                                "error_type": None,
                                "reason": None,
                                "command_id": created_command_id,
                                "command_result_status": command_status,
                                "metadata": dict(strategy_meta),
                            }
                        )
                    except Exception:
                        pass
                return RetryResult(
                    success=True,
                    command_id=created_command_id,
                    attempts_used=attempt,
                    final_strategy=current_strategy,
                    logs=logs,
                )

            after_screen = await self._take_screenshot()
            error_analysis = self._detect_error_fn(
                before_screen.image_bytes,
                after_screen.image_bytes,
                instruction,
                expected_outcome,
            )
            logs.append(
                RecoveryLog(
                    attempt=attempt,
                    strategy=current_strategy,
                    command_id=created_command_id,
                    status="failed",
                    command_result_status=command_status,
                    error_type=error_analysis.error_type,
                    reason=error_analysis.reason,
                    metadata={
                        **strategy_meta,
                        "suggested_fix": error_analysis.suggested_fix,
                        "confidence": error_analysis.confidence,
                    },
                )
            )
            if record_attempt_fn:
                try:
                    await record_attempt_fn(
                        {
                            "attempt": attempt,
                            "strategy": current_strategy,
                            "action": action_to_run,
                            "args": dict(args_to_run),
                            "success": False,
                            "duration_ms": duration_ms,
                            "error_type": error_analysis.error_type,
                            "reason": error_analysis.reason,
                            "suggested_fix": error_analysis.suggested_fix,
                            "confidence": error_analysis.confidence,
                            "command_id": created_command_id,
                            "command_result_status": command_status,
                            "metadata": {
                                **strategy_meta,
                                "suggested_fix": error_analysis.suggested_fix,
                            },
                        }
                    )
                except Exception:
                    pass

            last_reason = error_analysis.reason
            used_strategies.append(current_strategy)
            current_strategy = self._pick_next_strategy(
                suggested=error_analysis.suggested_fix,
                preferred=preferred_strategies,
                used=used_strategies,
            )
            before_screen = after_screen
            args = args_to_run

        return RetryResult(
            success=False,
            attempts_used=max_safe_attempts,
            final_strategy=current_strategy,
            final_reason=last_reason or "recovery_attempts_exhausted",
            logs=logs,
        )
