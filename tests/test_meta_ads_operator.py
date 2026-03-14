from __future__ import annotations

from brain.meta_ads_operator import (
    ActionType,
    EntityType,
    MetaAdsMetrics,
    MetaAdsSnapshot,
    analyze_context_snapshot,
    analyze_meta_ads_snapshot,
)
from brain.meta_ads_operator.client import adapt_entity, normalize_metrics


def test_normalize_metrics_prefers_downstream_for_followup_signal() -> None:
    metrics = normalize_metrics(
        {
            "spend": "25.5",
            "impressions": "3200",
            "inline_link_clicks": "48",
            "inline_link_click_ctr": "3.6",
            "actions": [
                {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "12"},
            ],
        },
        downstream={
            "reply_rate": 0.18,
            "left_on_read_pct": 74,
            "first_response_minutes": 14,
        },
    )

    assert metrics.spend == 25.5
    assert metrics.messaging_conversations == 12
    assert metrics.reply_rate == 0.18
    assert metrics.left_on_read_pct == 74


def test_operator_prefers_followup_inspection_over_pause_for_good_ctr_bad_downstream() -> None:
    report = analyze_context_snapshot(
        {
            "campaign_name": "WA Clinica",
            "ctr_link_pct": 3.9,
            "spend": 36,
            "impressions": 4100,
            "link_clicks": 57,
            "reply_rate": 0.11,
            "left_on_read_pct": 81,
            "first_response_minutes": 22,
            "channel": "whatsapp",
        },
        risk_score=0.45,
    )

    entity_report = report.entity_reports[0]
    assert "poor_followup_downstream" in entity_report.flags
    assert entity_report.actions[0].action_type == ActionType.INSPECT_FOLLOWUP_FUNNEL


def test_operator_holds_steady_when_data_is_weak() -> None:
    report = analyze_context_snapshot(
        {
            "campaign_name": "Testing Low Spend",
            "ctr_link_pct": 0.8,
            "spend": 4,
            "impressions": 300,
            "link_clicks": 6,
        },
        risk_score=0.2,
    )

    entity_report = report.entity_reports[0]
    assert "insufficient_data" in entity_report.flags
    assert entity_report.actions[0].action_type == ActionType.HOLD_STEADY


def test_operator_detects_fatigue_and_marks_refresh() -> None:
    report = analyze_context_snapshot(
        {
            "campaign_name": "Fatigued Creative",
            "spend": 54,
            "impressions": 6200,
            "link_clicks": 80,
            "ctr_link_pct": 1.7,
            "frequency": 3.4,
            "ctr_drop_pct": 32,
        },
        risk_score=0.5,
    )

    entity_report = report.entity_reports[0]
    assert "creative_fatigue" in entity_report.flags
    assert any(item.action_type == ActionType.MARK_FOR_CREATIVE_REFRESH for item in entity_report.actions)


def test_operator_detects_over_fragmentation_on_snapshot() -> None:
    adsets = []
    for index in range(7):
        adsets.append(
            adapt_entity(
                {
                    "id": f"adset_{index}",
                    "name": f"Adset {index}",
                    "status": "ACTIVE",
                    "daily_budget": "5",
                    "insights": {
                        "data": [
                            {
                                "spend": "10",
                                "impressions": "2500",
                                "inline_link_clicks": "40",
                                "inline_link_click_ctr": "2.2",
                            }
                        ]
                    },
                },
                EntityType.ADSET,
            )
        )
    snapshot = MetaAdsSnapshot(account_id="act_123", objective="messages", adsets=adsets)
    report = analyze_meta_ads_snapshot(snapshot)

    assert "over_fragmentation" in report.flags

