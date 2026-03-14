import pytest

from brain.action_policy import is_sensitive_action, requires_human_approval
from schemas.action_contract import ActionContract


def _action(name: str = "lookup_catalog") -> ActionContract:
    return ActionContract(
        contract_version="1.0",
        action=name,
        integration="shopify",
        arguments={"sku": "A1"},
        rationale="Needed to fulfill the request",
    )


def test_action_contract_requires_version() -> None:
    with pytest.raises(Exception):
        ActionContract(
            action="lookup_catalog",
            integration="shopify",
            arguments={},
            rationale="test",
        )


def test_action_contract_forbids_unknown_fields() -> None:
    with pytest.raises(Exception):
        ActionContract.model_validate(
            {
                "contract_version": "1.0",
                "action": "lookup_catalog",
                "integration": "shopify",
                "arguments": {},
                "rationale": "test",
                "extra": "not-allowed",
            }
        )


def test_sensitive_policy_defaults_to_true_for_sensitive_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", raising=False)
    action = _action("create_order")

    assert is_sensitive_action(action) is True
    assert requires_human_approval(action) is True


def test_sensitive_policy_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", "false")
    action = _action("create_order")

    assert requires_human_approval(action) is False


def test_sensitive_policy_uses_custom_env_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENSITIVE_ACTIONS", "run_payroll,close_account")

    assert is_sensitive_action(_action("run_payroll")) is True
    assert is_sensitive_action(_action("lookup_catalog")) is False
