from .client import FakeMetaAdsClient, MetaAdsApiClient, MetaAdsClientProtocol
from .engine import analyze_context_snapshot, analyze_meta_ads_snapshot
from .models import (
    ActionProposal,
    ActionType,
    DiagnosticFinding,
    DiagnosticSeverity,
    EntityInsight,
    EntityReport,
    EntityType,
    MetaAdsMetrics,
    MetaAdsOperatorReport,
    MetaAdsSnapshot,
    OperatorThresholds,
)

__all__ = [
    "ActionProposal",
    "ActionType",
    "DiagnosticFinding",
    "DiagnosticSeverity",
    "EntityInsight",
    "EntityReport",
    "EntityType",
    "FakeMetaAdsClient",
    "MetaAdsApiClient",
    "MetaAdsClientProtocol",
    "MetaAdsMetrics",
    "MetaAdsOperatorReport",
    "MetaAdsSnapshot",
    "OperatorThresholds",
    "analyze_context_snapshot",
    "analyze_meta_ads_snapshot",
]
