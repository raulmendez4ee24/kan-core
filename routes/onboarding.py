import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException

from brain.autonomous_case_manager import create_or_update_case
from brain.onboarding import analyze_onboarding_intake
from brain.website_intake import inspect_target_website
from database import AsyncSessionLocal
from schemas.onboarding import OnboardingIntakeRequest, OnboardingIntakeResponse
from tools.n8n_client import send_to_n8n_with_response

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _validate_onboarding_key(x_onboarding_key: Optional[str]) -> None:
    expected = os.getenv("ONBOARDING_INTAKE_KEY")
    if not expected:
        return
    if x_onboarding_key != expected:
        raise HTTPException(status_code=401, detail="Invalid onboarding key")


@router.post("/intake", response_model=OnboardingIntakeResponse)
async def onboarding_intake(
    data: OnboardingIntakeRequest,
    x_onboarding_key: Optional[str] = Header(None, alias="X-Onboarding-Key"),
):
    _validate_onboarding_key(x_onboarding_key)

    intake_payload = data
    website_processed = False
    website_url: Optional[str] = None
    detected_api_docs = sorted(set(data.api_docs_urls))
    ingestion_notes: list[str] = []

    if data.target_url:
        inspected = await inspect_target_website(data.target_url)
        website_processed = bool(inspected.get("processed"))
        website_url = inspected.get("website_url") if isinstance(inspected, dict) else None

        website_text = (
            inspected.get("website_text")
            if isinstance(inspected, dict)
            else ""
        )
        if isinstance(website_text, str) and website_text.strip():
            enriched_metadata = dict(data.metadata)
            enriched_metadata["__website_text"] = website_text
            intake_payload = data.model_copy(update={"metadata": enriched_metadata})

        docs_from_site = inspected.get("api_docs_urls") if isinstance(inspected, dict) else []
        if isinstance(docs_from_site, list):
            detected_api_docs = sorted(
                set(detected_api_docs).union(str(url).strip() for url in docs_from_site if str(url).strip())
            )

        notes = inspected.get("notes") if isinstance(inspected, dict) else []
        if isinstance(notes, list):
            ingestion_notes.extend(str(item) for item in notes if str(item).strip())

    analysis = analyze_onboarding_intake(
        intake_payload,
        detected_api_docs=detected_api_docs,
        website_processed=website_processed,
        website_url=website_url,
        ingestion_notes=ingestion_notes,
    )
    case_enabled = os.getenv("ENABLE_AUTONOMOUS_CASE_MANAGER", "false").lower() in {"1", "true", "yes"}
    dispatch_payload = {
        "source": "onboarding_intake",
        "lead_id": intake_payload.lead_id,
        "client_id": intake_payload.client_id,
        "company_name": intake_payload.company_name,
        "contact": intake_payload.contact.model_dump(),
        "request_text": intake_payload.request_text,
        "target_url": intake_payload.target_url,
        "channels": intake_payload.channels,
        "desired_capabilities": intake_payload.desired_capabilities,
        "requested_appointment": (
            intake_payload.requested_appointment.model_dump()
            if intake_payload.requested_appointment
            else None
        ),
        "integrations": [item.model_dump() for item in intake_payload.integrations],
        "metadata": intake_payload.metadata,
        "detected_api_docs": detected_api_docs,
        "ingestion_notes": ingestion_notes,
        "analysis": analysis.model_dump(),
    }
    if case_enabled and intake_payload.client_id:
        try:
            org_id = UUID(str(intake_payload.client_id))
        except ValueError:
            org_id = None
        if org_id:
            async with AsyncSessionLocal() as session:
                case_result = await create_or_update_case(
                    session,
                    organization_id=org_id,
                    case_type="onboarding_execution",
                    current_goal=f"Procesar onboarding: {intake_payload.company_name or intake_payload.request_text[:80]}",
                    business_context={
                        "source": "onboarding_intake",
                        "lead_id": intake_payload.lead_id,
                        "client_id": intake_payload.client_id,
                        "company_name": intake_payload.company_name,
                        "request_text": intake_payload.request_text,
                        "case_key": str(intake_payload.lead_id or intake_payload.client_id),
                        "analysis": analysis.model_dump(),
                    },
                    run_now=True,
                )
            run_payload = dict(case_result.get("run") or {})
            execution_result = dict(run_payload.get("execution_result") or {})
            analysis.n8n_dispatched = bool(execution_result.get("dispatched") or execution_result.get("completed"))
            return analysis

    dispatch_result = await send_to_n8n_with_response(dispatch_payload)
    analysis.n8n_dispatched = bool(dispatch_result.get("dispatched"))
    return analysis
