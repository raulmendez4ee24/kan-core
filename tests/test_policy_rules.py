from uuid import uuid4

from brain.policy_engine import (
    EffectivePolicy,
    policy_requires_human_approval_for_action,
    validate_desktop_action,
)


def test_policy_denies_action() -> None:
    policy = EffectivePolicy(
        organization_id=uuid4(),
        policy={"rules": [{"deny_actions": ["open_program"]}]},
        updated_at=None,
        enforced=True,
    )
    err = validate_desktop_action(action="open_program", args={"name": "notepad"}, policy=policy)
    assert err and "denied" in err.lower()


def test_policy_requires_approval_when_numeric() -> None:
    policy = EffectivePolicy(
        organization_id=uuid4(),
        policy={
            "rules": [
                {
                    "require_approval_when": {
                        "action": "payments.charge",
                        "arg": "amount",
                        "op": ">",
                        "value": 1000,
                    }
                }
            ]
        },
        updated_at=None,
        enforced=True,
    )

    assert policy_requires_human_approval_for_action(
        action="payments.charge", args={"amount": 1500}, policy=policy
    )
    assert not policy_requires_human_approval_for_action(
        action="payments.charge", args={"amount": 500}, policy=policy
    )


def test_policy_requires_approval_when_dotted_path() -> None:
    policy = EffectivePolicy(
        organization_id=uuid4(),
        policy={
            "rules": [
                {
                    "require_approval_when": {
                        "action": "payments.charge",
                        "arg": "payment.amount",
                        "op": ">=",
                        "value": 10,
                    }
                }
            ]
        },
        updated_at=None,
        enforced=True,
    )

    assert policy_requires_human_approval_for_action(
        action="payments.charge", args={"payment": {"amount": 10}}, policy=policy
    )
    assert not policy_requires_human_approval_for_action(
        action="payments.charge", args={"payment": {"amount": 9}}, policy=policy
    )

