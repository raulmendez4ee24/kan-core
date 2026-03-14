from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.audit import write_audit_log
from brain.crm_engine import upsert_lead
from models import AuditLog
from tools.n8n_client import send_to_n8n_with_response


def _snapshot_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = metadata.get("rollback_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _targets_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    targets = snapshot.get("targets")
    return dict(targets) if isinstance(targets, dict) else {}


def _restore_payload_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    payload = snapshot.get("restore_payload")
    return dict(payload) if isinstance(payload, dict) else {}


async def _rollback_delete_customer(
    session: AsyncSession,
    *,
    organization_id: UUID,
    metadata: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    restore_payload = _restore_payload_from_snapshot(snapshot)
    if not restore_payload:
        raise ValueError("Rollback snapshot does not include restore_payload for delete_customer")

    lead = await upsert_lead(
        session,
        organization_id=organization_id,
        payload=restore_payload,
    )
    return {
        "domain": "delete_customer",
        "status": "restored",
        "lead_id": str(lead.id),
        "restored_fields": sorted(restore_payload.keys()),
        "context_signature": metadata.get("context_signature"),
    }


async def _rollback_payment(
    *,
    organization_id: UUID,
    metadata: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    targets = _targets_from_snapshot(snapshot)
    payment_reference = (
        targets.get("payment_id")
        or targets.get("charge_id")
        or targets.get("transaction_id")
        or targets.get("invoice_id")
        or targets.get("id")
        or targets.get("record_id")
    )
    amount = ((snapshot.get("impact") or {}) if isinstance(snapshot.get("impact"), dict) else {}).get("amount_usd")

    payload = {
        "source": "execution_guard.rollback",
        "organization_id": str(organization_id),
        "action": {
            "action": "refund_payment",
            "arguments": {
                "payment_reference": payment_reference,
                "amount_usd": amount,
                "rollback_of": metadata.get("context_signature"),
                "rollback_snapshot": snapshot,
            },
        },
        "rollback": {
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "reason": "execution_guard_compensation",
        },
    }
    result = await send_to_n8n_with_response(payload)
    return {
        "domain": "payment",
        "status": "dispatched" if bool(result.get("dispatched")) else "failed",
        "payment_reference": payment_reference,
        "execution_result": result,
    }


async def _rollback_email_send(
    *,
    organization_id: UUID,
    metadata: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    targets = _targets_from_snapshot(snapshot)
    restore_payload = _restore_payload_from_snapshot(snapshot)
    recipient = targets.get("email") or targets.get("to") or restore_payload.get("email")
    if not recipient:
        raise ValueError("Rollback snapshot does not include recipient email")

    subject = str(targets.get("subject") or "Actualizacion sobre correo anterior")
    compensation_subject = f"[AUTO-ROLLBACK] {subject}"[:180]
    body = (
        "Este mensaje corrige un envio automatico previo. "
        "Ignora el correo anterior si fue enviado por error. "
        f"Referencia: {metadata.get('context_signature') or 'n/a'}."
    )
    payload = {
        "source": "execution_guard.rollback",
        "organization_id": str(organization_id),
        "action": {
            "action": "send_email_compensation",
            "arguments": {
                "to": recipient,
                "subject": compensation_subject,
                "body": body,
                "rollback_of": metadata.get("context_signature"),
                "message_reference": targets.get("message_id"),
            },
        },
    }
    result = await send_to_n8n_with_response(payload)
    return {
        "domain": "email_send",
        "status": "dispatched" if bool(result.get("dispatched")) else "failed",
        "recipient": recipient,
        "execution_result": result,
    }


async def execute_rollback_from_metadata(
    session: AsyncSession,
    *,
    organization_id: UUID,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot = _snapshot_from_metadata(metadata)
    if not snapshot:
        raise ValueError("Audit log does not include rollback_snapshot")

    operation_type = str(metadata.get("operation_type") or snapshot.get("operation_type") or "").strip().lower()
    action_name = str(metadata.get("action_name") or snapshot.get("action") or "").strip().lower()

    if operation_type == "delete" and "customer" in action_name:
        result = await _rollback_delete_customer(
            session,
            organization_id=organization_id,
            metadata=metadata,
            snapshot=snapshot,
        )
    elif operation_type == "payment":
        result = await _rollback_payment(
            organization_id=organization_id,
            metadata=metadata,
            snapshot=snapshot,
        )
    elif operation_type == "messaging" or "email" in action_name or "mail" in action_name:
        result = await _rollback_email_send(
            organization_id=organization_id,
            metadata=metadata,
            snapshot=snapshot,
        )
    else:
        raise ValueError(f"No rollback strategy available for operation_type={operation_type or 'unknown'}")

    await write_audit_log(
        session,
        organization_id=organization_id,
        actor_user_id=None,
        action="execution_guard.rollback",
        resource="autonomy_action",
        resource_id=str(metadata.get("context_signature") or "") or None,
        status="ok" if str(result.get("status")) in {"restored", "dispatched"} else "failed",
        detail=str(result.get("domain") or "rollback"),
        metadata={
            "source_context_signature": metadata.get("context_signature"),
            "rollback_result": result,
        },
    )
    return result


async def run_execution_guard_rollback(
    session: AsyncSession,
    *,
    organization_id: UUID,
    audit_log_id: UUID,
) -> Dict[str, Any]:
    row = await session.get(AuditLog, audit_log_id)
    if not row or row.organization_id != organization_id:
        raise ValueError("Guard audit log not found")
    if str(row.action or "") not in {"execution_guard.preflight", "execution_guard.paused", "execution_guard.tripwire"}:
        raise ValueError("Audit log is not an execution guard event")

    metadata = dict(row.audit_metadata or {})
    result = await execute_rollback_from_metadata(
        session,
        organization_id=organization_id,
        metadata=metadata,
    )
    return {
        "audit_log_id": str(row.id),
        "source_action": row.action,
        "rollback": result,
    }
