import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import brain.execution_rollback as execution_rollback


class _Session:
    def __init__(self) -> None:
        self.logged = []

    async def get(self, _model, _id):
        return None


def test_execute_rollback_from_metadata_restores_customer(monkeypatch) -> None:
    async def fake_upsert_lead(*_args, **kwargs):
        assert kwargs["payload"]["email"] == "lead@example.com"
        return SimpleNamespace(id=uuid4())

    async def fake_write_audit_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execution_rollback, "upsert_lead", fake_upsert_lead)
    monkeypatch.setattr(execution_rollback, "write_audit_log", fake_write_audit_log)

    result = asyncio.run(
        execution_rollback.execute_rollback_from_metadata(
            _Session(),
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            metadata={
                "operation_type": "delete",
                "action_name": "delete_customer",
                "context_signature": "ctx-1",
                "rollback_snapshot": {
                    "action": "delete_customer",
                    "operation_type": "delete",
                    "restore_payload": {"email": "lead@example.com", "name": "Lead"},
                },
            },
        )
    )

    assert result["domain"] == "delete_customer"
    assert result["status"] == "restored"


def test_execute_rollback_from_metadata_dispatches_payment_refund(monkeypatch) -> None:
    async def fake_send_to_n8n(payload):
        assert payload["action"]["action"] == "refund_payment"
        assert payload["action"]["arguments"]["payment_reference"] == "pay_123"
        return {"dispatched": True, "execution_status": "accepted"}

    async def fake_write_audit_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execution_rollback, "send_to_n8n_with_response", fake_send_to_n8n)
    monkeypatch.setattr(execution_rollback, "write_audit_log", fake_write_audit_log)

    result = asyncio.run(
        execution_rollback.execute_rollback_from_metadata(
            _Session(),
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            metadata={
                "operation_type": "payment",
                "action_name": "create_payment",
                "context_signature": "ctx-2",
                "rollback_snapshot": {
                    "operation_type": "payment",
                    "impact": {"amount_usd": 99.0},
                    "targets": {"payment_id": "pay_123"},
                },
            },
        )
    )

    assert result["domain"] == "payment"
    assert result["status"] == "dispatched"


def test_execute_rollback_from_metadata_dispatches_email_compensation(monkeypatch) -> None:
    async def fake_send_to_n8n(payload):
        assert payload["action"]["action"] == "send_email_compensation"
        assert payload["action"]["arguments"]["to"] == "user@example.com"
        return {"dispatched": True, "execution_status": "accepted"}

    async def fake_write_audit_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execution_rollback, "send_to_n8n_with_response", fake_send_to_n8n)
    monkeypatch.setattr(execution_rollback, "write_audit_log", fake_write_audit_log)

    result = asyncio.run(
        execution_rollback.execute_rollback_from_metadata(
            _Session(),
            organization_id=UUID("11111111-1111-1111-1111-111111111111"),
            metadata={
                "operation_type": "messaging",
                "action_name": "send_email",
                "context_signature": "ctx-3",
                "rollback_snapshot": {
                    "operation_type": "messaging",
                    "targets": {"email": "user@example.com", "subject": "Hola"},
                },
            },
        )
    )

    assert result["domain"] == "email_send"
    assert result["status"] == "dispatched"
