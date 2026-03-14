import os
from typing import Iterable, Set

from schemas.action_contract import ActionContract

DEFAULT_SENSITIVE_ACTIONS = {
    "create_order",
    "cancel_order",
    "refund_order",
    "create_payment",
    "charge_customer",
    "transfer_funds",
    "withdraw_funds",
    "delete_customer",
    "delete_integration",
    "update_billing",
}


def _load_sensitive_actions() -> Set[str]:
    raw = os.getenv("SENSITIVE_ACTIONS")
    if not raw:
        return set(DEFAULT_SENSITIVE_ACTIONS)

    parsed: Set[str] = set()
    for token in raw.split(","):
        normalized = token.strip().lower()
        if normalized:
            parsed.add(normalized)
    return parsed or set(DEFAULT_SENSITIVE_ACTIONS)


def _contains_sensitive_keyword(action_name: str, keywords: Iterable[str]) -> bool:
    return any(keyword and keyword in action_name for keyword in keywords)


def is_sensitive_action(action: ActionContract) -> bool:
    action_name = action.action.strip().lower()
    sensitive = _load_sensitive_actions()
    return action_name in sensitive or _contains_sensitive_keyword(
        action_name,
        ["refund", "cancel", "delete", "charge", "withdraw", "transfer", "payment"],
    )


def requires_human_approval(action: ActionContract) -> bool:
    enabled = os.getenv("REQUIRE_HUMAN_APPROVAL_FOR_SENSITIVE_ACTIONS", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    if not enabled:
        return False
    return is_sensitive_action(action)
