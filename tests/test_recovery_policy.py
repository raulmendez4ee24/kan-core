from datetime import datetime, timezone
from uuid import UUID, uuid4

import routes.desktop_agent as desktop_route
from models import DesktopCommand, DesktopCommandResult


TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


def _command(metadata: dict) -> DesktopCommand:
    now = datetime.now(timezone.utc)
    return DesktopCommand(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_id=uuid4(),
        action="click",
        args={"x": 10, "y": 20},
        status="failed",
        timeout_ms=10000,
        requested_by_user_id=None,
        approved_by_user_id=None,
        approved_at=None,
        issued_at=None,
        error="failed",
        command_metadata=metadata,
        created_at=now,
        updated_at=now,
    )


def _result(status: str = "failed") -> DesktopCommandResult:
    now = datetime.now(timezone.utc)
    return DesktopCommandResult(
        id=uuid4(),
        organization_id=UUID(TEST_CLIENT_ID),
        agent_id=uuid4(),
        command_id=uuid4(),
        status=status,
        duration_ms=100,
        error=("error" if status != "success" else None),
        data={},
        received_at=now,
        created_at=now,
        updated_at=now,
    )


def test_should_trigger_recovery_false_when_human_approval_required() -> None:
    command = _command({"approval": {"required": True}})
    result = _result("failed")

    assert desktop_route._should_trigger_recovery(command, result) is False


def test_should_trigger_recovery_false_for_recovery_managed_commands() -> None:
    command = _command({"recovery_control": "managed"})
    result = _result("failed")

    assert desktop_route._should_trigger_recovery(command, result) is False


def test_should_trigger_recovery_true_for_failed_non_sensitive_command() -> None:
    command = _command({"source": "vision.analyze"})
    result = _result("failed")

    assert desktop_route._should_trigger_recovery(command, result) is True


def test_should_trigger_recovery_true_after_human_approval_was_granted() -> None:
    command = _command({"approval": {"required": True}})
    command.approved_at = datetime.now(timezone.utc)
    result = _result("failed")

    assert desktop_route._should_trigger_recovery(command, result) is True
