from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EntityType(str, Enum):
    CAMPAIGN = "campaign"
    ADSET = "adset"
    AD = "ad"


class DiagnosticSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, Enum):
    PAUSE_AD = "pause_ad"
    DUPLICATE_WINNER = "duplicate_winner"
    INCREASE_BUDGET = "increase_budget"
    DECREASE_BUDGET = "decrease_budget"
    MARK_FOR_CREATIVE_REFRESH = "mark_for_creative_refresh"
    HOLD_STEADY = "hold_steady"
    INSPECT_FOLLOWUP_FUNNEL = "inspect_followup_funnel"


@dataclass(slots=True)
class MetaAdsMetrics:
    spend: float = 0.0
    impressions: float = 0.0
    reach: float = 0.0
    frequency: float = 0.0
    clicks: float = 0.0
    link_clicks: float = 0.0
    ctr: float = 0.0
    link_ctr: float = 0.0
    cpm: float = 0.0
    cpc: float = 0.0
    leads: float = 0.0
    messaging_conversations: float = 0.0
    bookings: float = 0.0
    purchases: float = 0.0
    revenue: float = 0.0
    roas: float = 0.0
    cost_per_lead: float = 0.0
    cost_per_message: float = 0.0
    cost_per_booking: float = 0.0
    reply_rate: float = 0.0
    booked_rate: float = 0.0
    close_rate: float = 0.0
    qualified_rate: float = 0.0
    left_on_read_pct: float = 0.0
    first_response_minutes: float = 0.0
    audience_size: float = 0.0
    budget_change_pct: float = 0.0
    sales_change_pct: float = 0.0
    ctr_drop_pct: float = 0.0


@dataclass(slots=True)
class EntityInsight:
    entity_type: EntityType
    entity_id: str
    name: str
    status: str = "ACTIVE"
    objective: str = ""
    daily_budget: float = 0.0
    lifetime_budget: float = 0.0
    channel: str = "meta"
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: MetaAdsMetrics = field(default_factory=MetaAdsMetrics)


@dataclass(slots=True)
class MetaAdsSnapshot:
    account_id: str
    account_name: str = ""
    objective: str = "lead_generation"
    account_metadata: Dict[str, Any] = field(default_factory=dict)
    campaigns: List[EntityInsight] = field(default_factory=list)
    adsets: List[EntityInsight] = field(default_factory=list)
    ads: List[EntityInsight] = field(default_factory=list)


@dataclass(slots=True)
class DiagnosticFinding:
    code: str
    title: str
    severity: DiagnosticSeverity
    confidence: float
    risk_score: float
    explanation: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActionProposal:
    action_type: ActionType
    target_entity_type: EntityType
    target_entity_id: str
    confidence: float
    risk_score: float
    reason: str
    dry_run: bool = True
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntityReport:
    entity: EntityInsight
    findings: List[DiagnosticFinding] = field(default_factory=list)
    actions: List[ActionProposal] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)


@dataclass(slots=True)
class MetaAdsOperatorReport:
    snapshot: MetaAdsSnapshot
    executive_summary: str
    entity_reports: List[EntityReport]
    account_findings: List[DiagnosticFinding] = field(default_factory=list)
    account_actions: List[ActionProposal] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)


@dataclass(slots=True)
class OperatorThresholds:
    min_spend_for_decision: float = 12.0
    min_impressions_for_decision: float = 1500.0
    min_link_clicks_for_decision: float = 25.0
    low_link_ctr: float = 1.0
    strong_link_ctr: float = 3.0
    excellent_link_ctr: float = 5.0
    fatigue_frequency: float = 2.8
    fatigue_ctr_drop_pct: float = 25.0
    low_reply_rate: float = 0.25
    low_booked_rate: float = 0.15
    high_left_on_read_pct: float = 60.0
    slow_first_response_minutes: float = 10.0
    unstable_scaling_budget_change_pct: float = 20.0
    unstable_scaling_sales_change_pct: float = 5.0
    narrow_audience_size: float = 50000.0
    max_adsets_per_100_spend: float = 3.0
    min_roas_for_winner: float = 2.0
    min_close_rate_for_winner: float = 0.08
    high_risk_guardrail: float = 0.8

