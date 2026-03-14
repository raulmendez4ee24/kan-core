"""Cold Email Ops: API request/response and sheet row models (leads, inboxes, domains, campaign, costs)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- API Request/Response ---

class ColdEmailOpsRunRequest(BaseModel):
    spreadsheet_id: Optional[str] = Field(default=None, max_length=128)
    solo_audit: bool = Field(default=False, description="Only run Phase 0-1 (load + audit)")
    export_format: Literal["csv", "json", "n8n"] = Field(default="csv")
    include_export: bool = Field(default=True, description="Include Phase 8 export when no blockers")


class ColdEmailOpsRunResponse(BaseModel):
    resumen_ejecutivo: List[str] = Field(default_factory=list, max_length=10)
    bloqueos: List[str] = Field(default_factory=list)
    cola_de_hoy: Dict[str, Any] = Field(default_factory=dict)
    cosas_por_pagar_configurar: List[Dict[str, Any]] = Field(default_factory=list)
    archivos_generados: List[str] = Field(default_factory=list)
    log_decisiones: List[str] = Field(default_factory=list)
    audit: Optional[Dict[str, Any]] = None
    tabs_missing: Optional[List[Dict[str, Any]]] = None


# --- Sheet row models (aligned to tabs) ---

class LeadRow(BaseModel):
    lead_id: Optional[str] = None
    company: Optional[str] = None
    website: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    linkedin: Optional[str] = None
    country: Optional[str] = None
    segment: Optional[str] = None
    status: Optional[str] = None  # new|queued|sent|replied|bounced|invalid|do_not_contact
    last_contacted_at: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    personalization_snippet: Optional[str] = None
    email_validation_status: Optional[str] = None  # unknown|valid|invalid|risky
    score: Optional[int] = None  # 0-100

    model_config = ConfigDict(extra="allow")


class InboxRow(BaseModel):
    inbox_id: Optional[str] = None
    provider: Optional[str] = None  # gmail|outlook|smtp
    from_name: Optional[str] = None
    from_email: Optional[str] = None
    domain: Optional[str] = None
    warmup_status: Optional[str] = None  # off|warming|ready
    daily_limit: Optional[int] = None
    sent_today: Optional[int] = None
    health_score: Optional[int] = None  # 0-100
    last_error: Optional[str] = None
    rotation_group: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class DomainRow(BaseModel):
    domain: Optional[str] = None
    registrar: Optional[str] = None
    dns_status: Optional[str] = None  # missing|ok
    spf_status: Optional[str] = None
    dkim_status: Optional[str] = None
    dmarc_status: Optional[str] = None
    tracking_subdomain_status: Optional[str] = None
    tls_status: Optional[str] = None
    notes: Optional[str] = None
    monthly_cost_estimate: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CampaignRow(BaseModel):
    campaign_id: Optional[str] = None
    offer: Optional[str] = None
    target_segment: Optional[str] = None
    sequence: Optional[str] = None
    spacing_days: Optional[int] = None
    send_window_localtime: Optional[str] = None
    max_new_leads_per_day: Optional[int] = None
    personalization_rules: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CostRow(BaseModel):
    item: Optional[str] = None
    vendor: Optional[str] = None
    cost: Optional[str] = None
    billing_cycle: Optional[str] = None
    status: Optional[str] = None  # paid|unpaid
    due_date: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def dict_to_lead(row: Dict[str, Any]) -> LeadRow:
    return LeadRow(**{k: row.get(k) for k in LeadRow.model_fields if k in row})


def dict_to_inbox(row: Dict[str, Any]) -> InboxRow:
    return InboxRow(**{k: row.get(k) for k in InboxRow.model_fields if k in row})


def dict_to_domain(row: Dict[str, Any]) -> DomainRow:
    return DomainRow(**{k: row.get(k) for k in DomainRow.model_fields if k in row})


def dict_to_campaign(row: Dict[str, Any]) -> CampaignRow:
    return CampaignRow(**{k: row.get(k) for k in CampaignRow.model_fields if k in row})


def dict_to_cost(row: Dict[str, Any]) -> CostRow:
    return CostRow(**{k: row.get(k) for k in CostRow.model_fields if k in row})
