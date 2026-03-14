"""Fase 0: Expected column names per tab and validation (missing/extra columns)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

EXPECTED_COLUMNS: Dict[str, List[str]] = {
    "leads": [
        "lead_id", "company", "website", "first_name", "last_name", "title",
        "email", "linkedin", "country", "segment", "status", "last_contacted_at",
        "source", "notes", "personalization_snippet", "email_validation_status", "score",
    ],
    "inboxes": [
        "inbox_id", "provider", "from_name", "from_email", "domain", "warmup_status",
        "daily_limit", "sent_today", "health_score", "last_error", "rotation_group",
    ],
    "domains": [
        "domain", "registrar", "dns_status", "spf_status", "dkim_status", "dmarc_status",
        "tracking_subdomain_status", "tls_status", "notes", "monthly_cost_estimate",
    ],
    "campaign": [
        "campaign_id", "offer", "target_segment", "sequence", "spacing_days",
        "send_window_localtime", "max_new_leads_per_day", "personalization_rules",
    ],
    "costs": [
        "item", "vendor", "cost", "billing_cycle", "status", "due_date", "notes",
    ],
}


@dataclass
class SheetSchemaResult:
    valid: bool
    missing_columns: Dict[str, List[str]] = field(default_factory=dict)
    extra_columns: Dict[str, List[str]] = field(default_factory=dict)
    tab_has_headers: Dict[str, bool] = field(default_factory=dict)


def get_expected_columns(tab_name: str) -> List[str]:
    return list(EXPECTED_COLUMNS.get(tab_name, []))


def validate_tab_columns(
    tab_name: str,
    raw_rows: List[Dict[str, str]],
) -> SheetSchemaResult:
    """Check that first row keys match expected columns. Empty sheet = missing all."""
    expected = set(EXPECTED_COLUMNS.get(tab_name, []))
    missing: List[str] = []
    extra: List[str] = []
    has_headers = False
    if raw_rows:
        actual = set(k.strip() for k in raw_rows[0].keys() if k)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        has_headers = len(actual) > 0
    else:
        missing = sorted(expected)
    return SheetSchemaResult(
        valid=len(missing) == 0,
        missing_columns={tab_name: missing},
        extra_columns={tab_name: extra} if extra else {},
        tab_has_headers={tab_name: has_headers},
    )


def validate_all_tabs(data: Dict[str, List[Dict[str, str]]]) -> SheetSchemaResult:
    """Validate leads, inboxes, domains, campaign, costs. Aggregate missing/extra."""
    all_missing: Dict[str, List[str]] = {}
    all_extra: Dict[str, List[str]] = {}
    tab_has_headers: Dict[str, bool] = {}
    valid = True
    for tab in ["leads", "inboxes", "domains", "campaign", "costs"]:
        rows = data.get(tab, [])
        r = validate_tab_columns(tab, rows)
        if not r.valid:
            valid = False
        all_missing.update(r.missing_columns)
        all_extra.update(r.extra_columns)
        tab_has_headers.update(r.tab_has_headers)
    return SheetSchemaResult(
        valid=valid,
        missing_columns=all_missing,
        extra_columns=all_extra,
        tab_has_headers=tab_has_headers,
    )
