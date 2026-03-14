import asyncio
import base64
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from database import AsyncSessionLocal
from brain.policy_engine import load_effective_policy
from learning.strategy_ranker import rank_recovery_strategies
from learning.strategy_memory import build_ui_context, record_strategy_outcome
from models import DesktopCommand, DesktopCommandResult
from recovery.error_detector import detect_error
from recovery.retry_engine import RetryEngine, ScreenshotPayload
from recovery.schemas import RetryResult

logger = logging.getLogger("kan_core.recovery.service")

DispatchCommandFn = Callable[[AsyncSession, DesktopCommand], Awaitable[bool]]


def _feature_enabled() -> bool:
    return os.getenv("RECOVERY_ENABLED", "true").lower() in {"1", "true", "yes"}


def _max_recovery_attempts() -> int:
    raw = os.getenv("MAX_RECOVERY_ATTEMPTS", "3")
    try:
        value = int(raw)
    except ValueError:
        value = 3
    return max(1, min(value, 10))


def _wait_timeout_seconds() -> float:
    raw = os.getenv("RECOVERY_COMMAND_WAIT_TIMEOUT_SECONDS", "25")
    try:
        value = float(raw)
    except ValueError:
        value = 25.0
    return max(5.0, min(value, 120.0))


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}") from exc


def _extract_instruction(command: DesktopCommand) -> str:
    metadata = command.command_metadata or {}
    nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    candidates = [
        metadata.get("instruction"),
        nested.get("instruction") if isinstance(nested, dict) else None,
        nested.get("message") if isinstance(nested, dict) else None,
        metadata.get("message"),
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return f"Execute desktop action '{command.action}' with args {command.args or {}}"


def _extract_expected_outcome(command: DesktopCommand) -> str:
    metadata = command.command_metadata or {}
    nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    candidates = [
        metadata.get("expected_outcome"),
        nested.get("expected_outcome") if isinstance(nested, dict) else None,
        nested.get("rationale") if isinstance(nested, dict) else None,
        metadata.get("rationale"),
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return f"The action '{command.action}' should complete successfully."


def _extract_industry(command: DesktopCommand) -> str:
    metadata = command.command_metadata or {}
    nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    candidates = [
        metadata.get("industry"),
        nested.get("industry") if isinstance(nested, dict) else None,
        nested.get("business_industry") if isinstance(nested, dict) else None,
        nested.get("vertical") if isinstance(nested, dict) else None,
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip().lower()[:120]
    return "general"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_learning_ui_context(command: DesktopCommand) -> Dict[str, Any]:
    metadata = command.command_metadata or {}
    args = dict(command.args or {})
    context = build_ui_context(action=command.action, args=args, metadata=metadata)
    url = args.get("url") or args.get("start_url")
    if isinstance(url, str) and url.strip():
        host = urlparse(url).hostname
        if host:
            context["domain"] = host.lower()
    return context


def _requires_human_approval(command: DesktopCommand) -> bool:
    metadata = command.command_metadata or {}
    required = bool(metadata.get("requires_human_approval"))

    approval = metadata.get("approval")
    if isinstance(approval, dict) and bool(approval.get("required")):
        required = True

    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        nested_approval = nested.get("approval")
        if isinstance(nested_approval, dict) and bool(nested_approval.get("required")):
            required = True
    if not required:
        return False
    # Recovery is allowed only after explicit approval happened.
    return command.approved_at is None


def _is_recovery_managed(command: DesktopCommand) -> bool:
    metadata = command.command_metadata or {}
    return str(metadata.get("recovery_control") or "").lower() == "managed"


class RecoveryService:
    def __init__(self, *, dispatch_command: DispatchCommandFn) -> None:
        self._dispatch_command = dispatch_command
        self._wait_timeout = _wait_timeout_seconds()

    async def _get_command(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        command_id: UUID,
    ) -> Optional[DesktopCommand]:
        stmt = select(DesktopCommand).where(
            DesktopCommand.organization_id == organization_id,
            DesktopCommand.id == command_id,
        )
        return (await session.execute(stmt)).scalars().first()

    async def _get_result(
        self,
        session: AsyncSession,
        *,
        command_id: UUID,
    ) -> Optional[DesktopCommandResult]:
        stmt = select(DesktopCommandResult).where(DesktopCommandResult.command_id == command_id)
        return (await session.execute(stmt)).scalars().first()

    async def _create_recovery_command(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        agent_id: UUID,
        action: str,
        args: Dict[str, Any],
        timeout_ms: int,
        origin_command_id: UUID,
        attempt: int,
        strategy: str,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> DesktopCommand:
        command = DesktopCommand(
            organization_id=organization_id,
            agent_id=agent_id,
            action=action,
            args=args,
            status="approved",
            timeout_ms=max(500, min(timeout_ms, 120000)),
            requested_by_user_id=None,
            approved_by_user_id=None,
            approved_at=datetime.now(timezone.utc),
            command_metadata={
                "source": "recovery.auto_repair",
                "recovery_control": "managed",
                "recovery_origin_command_id": str(origin_command_id),
                "recovery_attempt": attempt,
                "recovery_strategy": strategy,
                **(extra_metadata or {}),
            },
        )
        session.add(command)
        await session.commit()
        await session.refresh(command)
        return command

    async def _wait_for_result(
        self,
        session: AsyncSession,
        *,
        command_id: UUID,
    ) -> Optional[DesktopCommandResult]:
        started = asyncio.get_event_loop().time()
        while True:
            row = await self._get_result(session, command_id=command_id)
            if row:
                return row
            elapsed = asyncio.get_event_loop().time() - started
            if elapsed >= self._wait_timeout:
                return None
            await asyncio.sleep(0.4)

    async def _execute_action(
        self,
        organization_id: UUID,
        agent_id: UUID,
        origin_command_id: UUID,
        *,
        action: str,
        args: Dict[str, Any],
        timeout_ms: int,
        attempt: int,
        strategy: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            command = await self._create_recovery_command(
                session,
                organization_id=organization_id,
                agent_id=agent_id,
                action=action,
                args=args,
                timeout_ms=timeout_ms,
                origin_command_id=origin_command_id,
                attempt=attempt,
                strategy=strategy,
                extra_metadata=metadata,
            )

            dispatched = await self._dispatch_command(session, command=command)
            if not dispatched:
                command.status = "failed"
                command.error = "agent_offline_or_dispatch_failed"
                await session.commit()
                return {
                    "status": "failed",
                    "command_id": str(command.id),
                    "reason": "dispatch_failed",
                }

            result = await self._wait_for_result(session, command_id=command.id)
            if not result:
                command.status = "failed"
                command.error = "result_timeout"
                await session.commit()
                return {
                    "status": "failed",
                    "command_id": str(command.id),
                    "reason": "result_timeout",
                }

            status = "success" if result.status == "success" else "failed"
            return {
                "status": status,
                "command_id": str(command.id),
                "result_error": result.error,
                "result_data": result.data or {},
                "result_duration_ms": result.duration_ms,
            }

    async def _take_screenshot(
        self,
        *,
        organization_id: UUID,
        agent_id: UUID,
        origin_command_id: UUID,
        attempt: int,
        strategy: str,
        screenshot_action: str = "take_screenshot",
    ) -> ScreenshotPayload:
        result = await self._execute_action(
            organization_id,
            agent_id,
            origin_command_id,
            action=screenshot_action,
            args={"max_width": 1600},
            timeout_ms=20000,
            attempt=attempt,
            strategy=strategy,
            metadata={"recovery_capture": True},
        )
        if result.get("status") != "success":
            raise RuntimeError(f"screenshot_failed:{result.get('reason')}")

        payload = result.get("result_data") or {}
        image_base64 = payload.get("image_base64")
        if not isinstance(image_base64, str) or not image_base64.strip():
            raise RuntimeError("screenshot_payload_missing_image")
        image_bytes = base64.b64decode(image_base64)
        width = int(payload.get("width") or 1)
        height = int(payload.get("height") or 1)
        return ScreenshotPayload(
            image_bytes=image_bytes,
            width=max(1, width),
            height=max(1, height),
            metadata={"command_id": result.get("command_id")},
        )

    async def _write_recovery_audit(
        self,
        *,
        organization_id: UUID,
        command_id: str,
        status: str,
        detail: Optional[str],
        metadata: Dict[str, Any],
    ) -> None:
        async with AsyncSessionLocal() as session:
            try:
                await write_audit_log(
                    session,
                    organization_id=organization_id,
                    actor_user_id=None,
                    action="desktop_agent.recovery",
                    resource="desktop_command",
                    resource_id=command_id,
                    status=status,
                    detail=detail,
                    metadata=metadata,
                )
            except Exception:
                logger.exception("Failed to write recovery audit")

    async def execute_with_recovery(
        self,
        *,
        organization_id: UUID,
        failed_command_id: UUID,
    ) -> RetryResult:
        async with AsyncSessionLocal() as session:
            command = await self._get_command(
                session,
                organization_id=organization_id,
                command_id=failed_command_id,
            )
            if not command:
                return RetryResult(success=False, final_reason="command_not_found")
            if _is_recovery_managed(command):
                return RetryResult(success=False, final_reason="command_managed_by_recovery")
            if _requires_human_approval(command):
                return RetryResult(success=False, final_reason="blocked_human_approval_required")

            result = await self._get_result(session, command_id=command.id)
            if not result or result.status == "success":
                return RetryResult(success=False, final_reason="command_not_failed")

            metadata = command.command_metadata or {}
            if int(metadata.get("recovery_attempt") or 0) >= _max_recovery_attempts():
                return RetryResult(success=False, final_reason="recovery_attempt_limit_reached")

            instruction = _extract_instruction(command)
            expected_outcome = _extract_expected_outcome(command)
            industry = _extract_industry(command)
            timeout_ms = command.timeout_ms
            agent_id = command.agent_id
            original_action = command.action
            original_args = dict(command.args or {})
            learning_ui_context = _build_learning_ui_context(command)
            learning_metadata = dict(command.command_metadata or {})
            screenshot_action = (
                "browser_screenshot" if str(original_action).startswith("browser_") else "take_screenshot"
            )

        strategy_preferences: list[str] = []
        try:
            async with AsyncSessionLocal() as learning_session:
                # Inject org-calibrated exploration epsilon into metadata if policies are enabled.
                policy = await load_effective_policy(learning_session, organization_id=organization_id)
                learning_metadata = dict(learning_metadata)
                learning_metadata["learning_exploration_epsilon"] = policy.learning_exploration_epsilon()
                strategy_preferences = list(
                    await rank_recovery_strategies(
                        learning_session,
                        organization_id=str(organization_id),
                        industry=industry,
                        action=original_action,
                        args=original_args,
                        metadata=learning_metadata,
                        candidates=[
                            "search_again",
                            "adjust_coordinates",
                            "retry_with_alternate_selector",
                            "scroll_and_retry",
                            "wait_and_retry",
                            "fallback_click_center",
                        ],
                    )
                )
        except Exception:
            logger.exception("Failed to load strategy preferences from learning memory")

        async def _record_attempt(event: Dict[str, Any]) -> None:
            try:
                async with AsyncSessionLocal() as learning_session:
                    await record_strategy_outcome(
                        learning_session,
                        organization_id=str(organization_id),
                        industry=industry,
                        action=original_action,
                        strategy=str(event.get("strategy") or "search_again"),
                        ui_context=learning_ui_context,
                        success=bool(event.get("success")),
                        duration_ms=_safe_int(event.get("duration_ms")),
                        reason=(str(event.get("reason"))[:300] if event.get("reason") else None),
                        error_type=(
                            str(event.get("error_type"))[:120]
                            if event.get("error_type")
                            else None
                        ),
                        extra={
                            "attempt": event.get("attempt"),
                            "command_id": event.get("command_id"),
                            "command_result_status": event.get("command_result_status"),
                            "suggested_fix": event.get("suggested_fix"),
                            "confidence": event.get("confidence"),
                            "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
                        },
                    )
            except Exception:
                logger.exception("Failed to persist strategy learning event")

        initial_screen = await self._take_screenshot(
            organization_id=organization_id,
            agent_id=agent_id,
            origin_command_id=failed_command_id,
            attempt=0,
            strategy="search_again",
            screenshot_action=screenshot_action,
        )
        initial_error = await asyncio.to_thread(
            detect_error,
            initial_screen.image_bytes,
            initial_screen.image_bytes,
            instruction,
            expected_outcome,
        )

        engine = RetryEngine(
            take_screenshot=lambda: self._take_screenshot(
                organization_id=organization_id,
                agent_id=agent_id,
                origin_command_id=failed_command_id,
                attempt=0,
                strategy="search_again",
                screenshot_action=screenshot_action,
            ),
            execute_action=lambda action, args, meta: self._execute_action(
                organization_id,
                agent_id,
                failed_command_id,
                action=action,
                args=args,
                timeout_ms=timeout_ms,
                attempt=int(meta.get("recovery_attempt") or 1),
                strategy=str(meta.get("recovery_strategy") or "search_again"),
                metadata=meta,
            ),
            detect_error_fn=detect_error,
        )

        retry = await engine.retry_action(
            str(failed_command_id),
            initial_error.suggested_fix,
            max_attempts=_max_recovery_attempts(),
            original_action=original_action,
            original_args=original_args,
            instruction=instruction,
            expected_outcome=expected_outcome,
            strategy_preferences=strategy_preferences,
            record_attempt_fn=_record_attempt,
        )

        await self._write_recovery_audit(
            organization_id=organization_id,
            command_id=str(failed_command_id),
            status=("ok" if retry.success else "failed"),
            detail=retry.final_reason,
            metadata={
                "attempts_used": retry.attempts_used,
                "final_strategy": retry.final_strategy,
                "log_count": len(retry.logs),
            },
        )
        return retry


async def trigger_recovery_for_failed_command(
    *,
    organization_id: str,
    failed_command_id: str,
    dispatch_command: DispatchCommandFn,
) -> RetryResult:
    if not _feature_enabled():
        return RetryResult(success=False, final_reason="recovery_disabled")

    try:
        org_uuid = _parse_uuid(organization_id, field="organization_id")
        cmd_uuid = _parse_uuid(failed_command_id, field="failed_command_id")
    except ValueError as exc:
        return RetryResult(success=False, final_reason=str(exc))

    service = RecoveryService(dispatch_command=dispatch_command)
    try:
        return await service.execute_with_recovery(
            organization_id=org_uuid,
            failed_command_id=cmd_uuid,
        )
    except Exception as exc:
        logger.exception("Recovery execution failed")
        return RetryResult(success=False, final_reason=f"recovery_execution_error: {exc!s}")
