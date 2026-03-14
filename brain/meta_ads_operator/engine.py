from __future__ import annotations

from typing import Any, Dict, List

from .client import adapt_entity
from .diagnostics import detect_findings, detect_over_fragmentation
from .models import (
    DiagnosticFinding,
    EntityInsight,
    EntityType,
    MetaAdsOperatorReport,
    MetaAdsSnapshot,
    OperatorThresholds,
)
from .playbooks import recommendations_for_entity
from .reporting import build_report


def _select_entities(snapshot: MetaAdsSnapshot) -> List[EntityInsight]:
    if snapshot.ads:
        return list(snapshot.ads)
    if snapshot.adsets:
        return list(snapshot.adsets)
    return list(snapshot.campaigns)


def analyze_meta_ads_snapshot(
    snapshot: MetaAdsSnapshot,
    *,
    thresholds: OperatorThresholds | None = None,
) -> MetaAdsOperatorReport:
    thresholds = thresholds or OperatorThresholds()
    entities = _select_entities(snapshot)
    entity_reports = []
    account_findings: List[DiagnosticFinding] = []
    reasons: List[str] = []
    flags: List[str] = []
    account_actions = []

    for entity in entities:
        findings, _ = detect_findings(entity, thresholds)
        report = recommendations_for_entity(entity, findings, thresholds=thresholds)
        entity_reports.append(report)
        for finding in findings:
            if finding.code not in flags:
                flags.append(finding.code)
        for action in report.actions:
            if action.action_type.value not in reasons:
                reasons.append(action.action_type.value)

    fragment_findings = detect_over_fragmentation(snapshot.adsets or entities, thresholds)
    account_findings.extend(fragment_findings)
    for finding in fragment_findings:
        if finding.code not in flags:
            flags.append(finding.code)

    return build_report(
        snapshot,
        entity_reports=entity_reports,
        account_findings=account_findings,
        account_actions=account_actions,
        reasons=reasons,
        flags=flags,
    )


def analyze_context_snapshot(
    context: Dict[str, Any],
    *,
    risk_score: float = 0.0,
    thresholds: OperatorThresholds | None = None,
) -> MetaAdsOperatorReport:
    normalized_context = dict(context or {})
    raw_entity = {
        "id": str(normalized_context.get("campaign_id") or normalized_context.get("ad_id") or "context_snapshot"),
        "name": str(normalized_context.get("campaign_name") or normalized_context.get("ad_name") or "Context Snapshot"),
        "status": normalized_context.get("status") or "ACTIVE",
        "objective": normalized_context.get("objective") or "OUTCOME_LEADS",
        "daily_budget": normalized_context.get("budget_usd") or normalized_context.get("daily_budget_usd"),
        "insights": {"data": [normalized_context]},
    }
    if risk_score > 0:
        normalized_context.setdefault("operator_risk_score", risk_score)
    entity = adapt_entity(raw_entity, EntityType.AD, downstream=normalized_context)
    snapshot = MetaAdsSnapshot(
        account_id=str(normalized_context.get("account_id") or "context_account"),
        account_name=str(normalized_context.get("account_name") or "Context Account"),
        objective=str(normalized_context.get("business_type") or "lead_generation"),
        ads=[entity],
        account_metadata={
            "source": "marketing_agent_context",
            "channel": normalized_context.get("channel") or "meta",
            "operator_risk_score": risk_score,
        },
    )
    return analyze_meta_ads_snapshot(snapshot, thresholds=thresholds)
