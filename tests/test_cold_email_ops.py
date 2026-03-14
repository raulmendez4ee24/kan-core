"""Tests for Cold Email Ops: schemas, audit, orchestrator, route."""
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import main
import routes.cold_email_ops as cold_email_route
from brain.cold_email_ops.audit import run_audit
from brain.cold_email_ops.copy_gen import run as run_copy_gen
from brain.cold_email_ops.inbox_rotation import run as run_inbox_rotation
from brain.cold_email_ops.sheets_schema import validate_all_tabs, validate_tab_columns
from brain.cold_email_ops.orchestrator import _run_phases_2_to_9, run_cold_email_ops
from brain.cold_email_ops.who_to_send import run as run_who_to_send
from schemas.cold_email_ops import ColdEmailOpsRunRequest, ColdEmailOpsRunResponse
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


def _minimal_leads_rows() -> List[Dict[str, Any]]:
    return [
        {
            "lead_id": "1",
            "company": "Acme",
            "website": "https://acme.com",
            "first_name": "Jane",
            "last_name": "Doe",
            "title": "CEO",
            "email": "jane@acme.com",
            "linkedin": "",
            "country": "US",
            "segment": "SMB",
            "status": "new",
            "last_contacted_at": "",
            "source": "manual",
            "notes": "",
            "personalization_snippet": "",
            "email_validation_status": "unknown",
            "score": "",
        },
    ]


def _minimal_inboxes_rows() -> List[Dict[str, Any]]:
    return [
        {
            "inbox_id": "inbox1",
            "provider": "gmail",
            "from_name": "Sales",
            "from_email": "sales@example.com",
            "domain": "example.com",
            "warmup_status": "ready",
            "daily_limit": 50,
            "sent_today": 0,
            "health_score": 100,
            "last_error": "",
            "rotation_group": "A",
        },
    ]


def _minimal_domains_rows() -> List[Dict[str, Any]]:
    return [
        {
            "domain": "example.com",
            "registrar": "Gandi",
            "dns_status": "ok",
            "spf_status": "ok",
            "dkim_status": "ok",
            "dmarc_status": "ok",
            "tracking_subdomain_status": "ok",
            "tls_status": "ok",
            "notes": "",
            "monthly_cost_estimate": "",
        },
    ]


def _minimal_campaign_rows() -> List[Dict[str, Any]]:
    return [
        {
            "campaign_id": "c1",
            "offer": "Demo",
            "target_segment": "SMB",
            "sequence": "3 steps",
            "spacing_days": 2,
            "send_window_localtime": "09:00-17:00",
            "max_new_leads_per_day": 20,
            "personalization_rules": "",
        },
    ]


def _minimal_costs_rows() -> List[Dict[str, Any]]:
    return [
        {"item": "Domain", "vendor": "Reg", "cost": "10", "billing_cycle": "monthly", "status": "paid", "due_date": "", "notes": ""},
    ]


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_validate_tab_columns_empty() -> None:
    r = validate_tab_columns("leads", [])
    assert r.valid is False
    assert "leads" in r.missing_columns
    assert len(r.missing_columns["leads"]) > 0


def test_validate_tab_columns_ok() -> None:
    rows = [dict(zip(_minimal_leads_rows()[0].keys(), _minimal_leads_rows()[0].values()))]
    r = validate_tab_columns("leads", rows)
    assert r.valid is True


def test_validate_all_tabs_minimal() -> None:
    data = {
        "leads": _minimal_leads_rows(),
        "inboxes": _minimal_inboxes_rows(),
        "domains": _minimal_domains_rows(),
        "campaign": _minimal_campaign_rows(),
        "costs": _minimal_costs_rows(),
    }
    r = validate_all_tabs(data)
    assert r.valid is True


def test_run_audit_no_blockers() -> None:
    data = {
        "leads": _minimal_leads_rows(),
        "inboxes": _minimal_inboxes_rows(),
        "domains": _minimal_domains_rows(),
        "campaign": _minimal_campaign_rows(),
        "costs": _minimal_costs_rows(),
    }
    out = run_audit(data)
    assert out.bloqueos == []
    assert len(out.resumen) >= 1


def test_run_audit_blockers_missing_inbox() -> None:
    data = {
        "leads": _minimal_leads_rows(),
        "inboxes": [],
        "domains": _minimal_domains_rows(),
        "campaign": _minimal_campaign_rows(),
        "costs": _minimal_costs_rows(),
    }
    out = run_audit(data)
    assert any("inbox" in b.lower() for b in out.bloqueos)


def test_orchestrator_missing_spreadsheet_id() -> None:
    req = ColdEmailOpsRunRequest(spreadsheet_id="", solo_audit=True)
    r = run_cold_email_ops(req)
    assert r.bloqueos
    assert any("spreadsheet" in b.lower() for b in r.bloqueos)


def test_orchestrator_solo_audit_with_mock_data(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_data = {
        "leads": _minimal_leads_rows(),
        "inboxes": _minimal_inboxes_rows(),
        "domains": _minimal_domains_rows(),
        "campaign": _minimal_campaign_rows(),
        "costs": _minimal_costs_rows(),
    }

    def fake_read_all_tabs(sid: str) -> Dict[str, List[Dict[str, Any]]]:
        return mock_data

    monkeypatch.setattr("tools.google_sheets_client.read_all_tabs", fake_read_all_tabs)
    req = ColdEmailOpsRunRequest(spreadsheet_id="mock-id", solo_audit=True)
    r = run_cold_email_ops(req)
    assert r.audit is not None
    assert r.audit["resumen"]
    assert not r.bloqueos


def test_cold_email_ops_route_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_COLD_EMAIL_OPS", "false")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    response = client.post("/cold-email-ops/run", json={})
    assert response.status_code == 404


def test_cold_email_ops_route_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_COLD_EMAIL_OPS", "true")
    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    response = client.post(
        "/cold-email-ops/run",
        json={"spreadsheet_id": "", "solo_audit": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert "bloqueos" in body
    assert "resumen_ejecutivo" in body
    assert "log_decisiones" in body


def test_who_to_send_applies_campaign_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    leads = [
        {**_minimal_leads_rows()[0], "lead_id": "1", "segment": "SMB", "status": "new", "email_validation_status": "valid"},
        {**_minimal_leads_rows()[0], "lead_id": "2", "segment": "enterprise", "status": "new", "email_validation_status": "valid"},
        {
            **_minimal_leads_rows()[0],
            "lead_id": "3",
            "segment": "SMB",
            "status": "sent",
            "last_contacted_at": "due",
            "notes": "Initial contact [ce_meta step=1/3 campaign=c1]",
            "email_validation_status": "risky",
        },
        {
            **_minimal_leads_rows()[0],
            "lead_id": "4",
            "segment": "SMB",
            "status": "sent",
            "last_contacted_at": "",
            "notes": "Initial contact [ce_meta step=1/3 campaign=c1]",
            "email_validation_status": "valid",
        },
    ]
    campaign = [
        {
            **_minimal_campaign_rows()[0],
            "campaign_id": "c1",
            "target_segment": "SMB",
            "sequence": "3 steps",
            "spacing_days": 2,
            "send_window_localtime": "09:00-17:00",
            "max_new_leads_per_day": 10,
        }
    ]
    data = {
        "leads": leads,
        "campaign": campaign,
        "inboxes": _minimal_inboxes_rows(),
    }
    cola: Dict[str, Any] = {}

    monkeypatch.setattr("brain.cold_email_ops.who_to_send._is_follow_up_due", lambda value, spacing: value == "due")

    _, _, result = run_who_to_send(data, [], cola)

    assert result["total_eligible"] == 2
    assert [row["lead_id"] for row in result["leads"]] == ["1", "3"]
    assert result["leads"][0]["status"] == "queued"
    assert result["leads"][0]["sequence_step"] == 1
    assert result["leads"][0]["_queued_from_status"] == "new"
    assert result["leads"][1]["sequence_step"] == 2
    assert result["leads"][1]["campaign_id"] == "c1"
    assert "step=2/3" in result["leads"][1]["notes"]
    assert leads[1]["status"] == "new"
    assert leads[3]["status"] == "sent"


def test_inbox_rotation_assigns_sender_and_prunes_unassigned() -> None:
    first = {"lead_id": "1", "status": "queued", "_queued_from_status": "new"}
    second = {"lead_id": "2", "status": "queued", "_queued_from_status": "sent"}
    queue = [first, second]
    data = {
        "inboxes": [{**_minimal_inboxes_rows()[0], "daily_limit": 1, "sent_today": 0}],
        "_cola_de_hoy": queue,
    }

    updated, log = run_inbox_rotation(data, [])

    assert len(updated["_cola_de_hoy"]) == 1
    assert updated["_cola_de_hoy"][0]["lead_id"] == "1"
    assert updated["_cola_de_hoy"][0]["from_email"] == "sales@example.com"
    assert updated["_cola_de_hoy"][0]["inbox_id"] == "inbox1"
    assert second["status"] == "sent"
    assert "from_email" not in second
    assert "_queued_from_status" not in second
    assert any("1 leads assigned" in entry for entry in log)


def test_copy_gen_builds_subject_and_body() -> None:
    data = {
        "leads": [
            {
                **_minimal_leads_rows()[0],
                "lead_id": "1",
                "company": "Acme Dental",
                "first_name": "Jane",
                "segment": "SMB",
                "sequence_step": 2,
                "sequence_total": 3,
                "personalization_snippet": "I saw Acme Dental has strong reviews in Guadalajara.",
            }
        ],
        "campaign": [
            {
                **_minimal_campaign_rows()[0],
                "offer": "a 15-minute teardown of your outbound funnel",
                "sequence": "3 steps",
                "send_window_localtime": "09:00-17:00",
                "personalization_rules": "Keep it concise and concrete.",
            }
        ],
    }

    updated, log = run_copy_gen(data, [])
    lead = updated["leads"][0]

    assert "Keep it concise and concrete." in lead["personalization_snippet"]
    assert "follow" in lead["subject"].lower()
    assert "15-minute teardown" in lead["body"]
    assert "Quick follow-up (2/3)" in lead["body"]
    assert any("subject and body" in entry for entry in log)


def test_orchestrator_runs_final_writeback_after_reply_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.cold_email_ops import copy_gen, domains_costs, email_verification, export as export_mod
    from brain.cold_email_ops import inbox_rotation, lead_cleanup, reply_monitor, sheet_writeback, who_to_send

    calls: List[str] = []

    monkeypatch.setattr(lead_cleanup, "run", lambda spreadsheet_id, data, log, resumen, archivos: (data, log, resumen, archivos))
    monkeypatch.setattr(email_verification, "run", lambda data, log, resumen: (data, log, resumen))

    def fake_who_to_send(data: Dict[str, Any], log: List[str], cola: Dict[str, Any]):
        cola["leads"] = data["leads"]
        return data, log, cola

    monkeypatch.setattr(who_to_send, "run", fake_who_to_send)
    monkeypatch.setattr(domains_costs, "run", lambda data, items, log: (items, log))
    monkeypatch.setattr(inbox_rotation, "run", lambda data, log: (data, log))
    monkeypatch.setattr(copy_gen, "run", lambda data, log: (data, log))
    monkeypatch.setattr(export_mod, "run", lambda data, cola, export_format, archivos, log: (archivos, log))

    def fake_reply_monitor(data: Dict[str, Any], log: List[str]):
        data["leads"][0]["status"] = "replied"
        return data, log

    monkeypatch.setattr(reply_monitor, "run", fake_reply_monitor)

    def fake_sheet_writeback(spreadsheet_id: str, data: Dict[str, Any], log: List[str]):
        calls.append(data["leads"][0]["status"])
        return data, log

    monkeypatch.setattr(sheet_writeback, "run", fake_sheet_writeback)

    _run_phases_2_to_9(
        spreadsheet_id="sheet-1",
        data={
            "leads": [{"lead_id": "1", "status": "queued"}],
            "inboxes": _minimal_inboxes_rows(),
            "campaign": _minimal_campaign_rows(),
            "domains": _minimal_domains_rows(),
            "costs": _minimal_costs_rows(),
        },
        log=[],
        resumen=[],
        bloqueos=[],
        cola_de_hoy={},
        cosas_por_pagar_configurar=[],
        archivos_generados=[],
        export_format="csv",
        include_export=False,
    )

    assert calls == ["queued", "replied"]
