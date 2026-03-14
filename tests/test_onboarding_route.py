from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import main
import routes.onboarding as onboarding_route


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_onboarding_intake_detects_missing_integrations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(onboarding_route, "send_to_n8n_with_response", fake_send)

    response = client.post(
        "/onboarding/intake",
        json={
            "company_name": "Clinica ACME",
            "request_text": "Quiero un bot de WhatsApp para agendar citas y cobrar anticipos.",
            "objective": "Agendar citas",
            "channels": ["whatsapp"],
            "desired_capabilities": ["agendar", "cobrar"],
            "integrations": [{"name": "meta", "status": "has_access"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_api_access"
    assert body["n8n_dispatched"] is True
    assert "calendar" in body["missing_integrations"]
    assert "payments" in body["missing_integrations"]
    assert "meta" in body["provided_integrations"]
    assert sent and sent[0]["source"] == "onboarding_intake"


def test_onboarding_intake_ready_for_automation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_send(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"dispatched": False, "pending": False, "confirmed": False, "execution_status": "not_dispatched"}

    monkeypatch.setattr(onboarding_route, "send_to_n8n_with_response", fake_send)

    response = client.post(
        "/onboarding/intake",
        json={
            "company_name": "Dental Uno",
            "request_text": "Necesito WhatsApp para agendar citas.",
            "channels": ["whatsapp"],
            "desired_capabilities": ["agenda de citas"],
            "integrations": [
                {"name": "meta", "status": "has_access"},
                {"name": "calendar", "status": "has_access"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready_for_automation"
    assert body["missing_integrations"] == []
    assert body["n8n_dispatched"] is False
    assert body["next_actions"]


def test_onboarding_requires_key_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONBOARDING_INTAKE_KEY", "secure-key")

    response = client.post(
        "/onboarding/intake",
        json={"request_text": "hola"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid onboarding key"


def test_onboarding_accepts_key_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONBOARDING_INTAKE_KEY", "secure-key")

    async def fake_send(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(onboarding_route, "send_to_n8n_with_response", fake_send)

    response = client.post(
        "/onboarding/intake",
        headers={"X-Onboarding-Key": "secure-key"},
        json={"request_text": "quiero bot para soporte por correo"},
    )

    assert response.status_code == 200
    assert response.json()["n8n_dispatched"] is True


def test_onboarding_intake_enriches_from_target_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_send(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    async def fake_inspect(_url: str) -> Dict[str, Any]:
        return {
            "processed": True,
            "website_url": "https://clinic.example.com",
            "website_text": "Atencion por WhatsApp para agendar citas y cobrar anticipos.",
            "api_docs_urls": ["https://docs.stripe.com/openapi.json"],
            "notes": ["analisis_web_ok"],
        }

    monkeypatch.setattr(onboarding_route, "send_to_n8n_with_response", fake_send)
    monkeypatch.setattr(onboarding_route, "inspect_target_website", fake_inspect)

    response = client.post(
        "/onboarding/intake",
        json={
            "request_text": "Quiero un asistente para ventas",
            "target_url": "https://clinic.example.com",
            "integrations": [{"name": "meta", "status": "has_access"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["website_processed"] is True
    assert body["website_url"] == "https://clinic.example.com"
    assert "https://docs.stripe.com/openapi.json" in body["detected_api_docs"]
    assert "analisis_web_ok" in body["ingestion_notes"]
    assert "calendar" in body["missing_integrations"]
    assert "payments" in body["missing_integrations"]
