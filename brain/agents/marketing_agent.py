from __future__ import annotations

import hashlib
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from brain.agents.creative_critic_agent import critique_creative_plan
from brain.agents.creative_strategist_agent import build_creative_plan
from brain.agents.offer_analyzer_agent import analyze_offer
from brain.anthropic_agent_runtime import AnthropicAgentRuntime, anthropic_agentic_enabled
from brain.creative_memory import get_creative_memory, record_creative_outcome
from brain.mcp_runtime import execute_tool
from brain.meta_ads_operator import ActionType, analyze_context_snapshot
from brain.skill_loader import load_skill

_ma_logger = logging.getLogger("kan_core.marketing_agent")

AGENT_MODEL = "claude-haiku-4-5-20251001"
THINKING_BUDGET = 6000
PRIMARY_SKILL_NAME = "meta_ads_growth"
SUPPORTING_SKILL_NAME = "marketing_execution"
OFFER_SKILL_NAME = "offer_analysis"
CREATIVE_SKILL_NAME = "creative_hooks"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_skill_summary() -> str:
    parts: List[str] = []
    for skill_name in (PRIMARY_SKILL_NAME, SUPPORTING_SKILL_NAME, OFFER_SKILL_NAME, CREATIVE_SKILL_NAME):
        loaded = load_skill(skill_name) or {}
        summary = str(loaded.get("summary") or "").strip()
        if summary:
            parts.append(summary)
    return " ".join(parts).strip()


def _resolve_campaign_id(context: Dict[str, Any]) -> str:
    """Extract a campaign identifier from *context*, or generate a stable one.

    Fallback hash includes domain, offer, adset/source, and date so that
    different campaigns on the same day for different offers or adsets get
    distinct identifiers instead of collapsing into one.
    """
    for key in ("campaign_id", "meta_campaign_id", "ad_campaign_id"):
        val = str(context.get(key) or "").strip()
        if val:
            return val
    parts = [
        str(context.get("domain") or "campaign_execution"),
        str(
            context.get("recommended_offer")
            or context.get("offer")
            or context.get("product")
            or ""
        ).strip().lower(),
        str(
            context.get("adset_id")
            or context.get("ad_set_id")
            or context.get("source")
            or context.get("traffic_source")
            or ""
        ).strip().lower(),
        str(
            context.get("audience")
            or context.get("vertical")
            or context.get("icp")
            or ""
        ).strip().lower(),
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    ]
    fingerprint = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"cmp-auto-{fingerprint}"


def _creative_stack(
    *,
    goal: str,
    context: Dict[str, Any],
    campaign_id: Optional[str] = None,
) -> Dict[str, Any]:
    cid = campaign_id or _resolve_campaign_id(context)
    offer_brief = analyze_offer(goal=goal, context=context)
    creative_plan = build_creative_plan(
        offer_brief=offer_brief, context=context, campaign_id=cid,
    )
    creative_review = critique_creative_plan(
        offer_brief=offer_brief, creative_plan=creative_plan,
    )
    saved_pattern = record_creative_outcome(
        domain="campaign_execution",
        creative_plan=creative_plan,
        critic_review=creative_review,
        offer=str(offer_brief.get("recommended_offer") or "Unknown"),
    )

    try:
        from brain.creative_event_tracker import (
            on_campaign_deployed,
            register_creative_pattern_usage,
        )

        if saved_pattern and saved_pattern.id:
            on_campaign_deployed(cid, saved_pattern.id, "campaign_execution")
            winning_idea = dict(creative_review.get("winner") or {})
            register_creative_pattern_usage(
                campaign_id=cid,
                domain="campaign_execution",
                offer=str(offer_brief.get("recommended_offer") or "Unknown"),
                hook=str(winning_idea.get("hook") or ""),
                angle=str(winning_idea.get("angle") or ""),
                cta=str(winning_idea.get("cta") or ""),
                pattern_used=str(winning_idea.get("pattern_used") or "unknown"),
                traffic_temperature=str(
                    winning_idea.get("traffic_temperature")
                    or creative_plan.get("traffic_temperature")
                    or ""
                ),
            )
            _ma_logger.info(
                "[MARKETING_AGENT] creative tracker: deployed "
                "campaign_id=%s pattern_id=%s",
                cid,
                saved_pattern.id,
            )
    except Exception:
        _ma_logger.warning(
            "[MARKETING_AGENT] creative tracker call failed "
            "(non-blocking) campaign_id=%s",
            cid,
            exc_info=True,
        )

    return {
        "offer_brief": offer_brief,
        "creative_plan": creative_plan,
        "creative_review": creative_review,
        "creative_memory": get_creative_memory(domain="campaign_execution"),
        "campaign_id": cid,
    }


_ACTION_TO_STRATEGY = {
    ActionType.MARK_FOR_CREATIVE_REFRESH: "refresh_creative",
    ActionType.INSPECT_FOLLOWUP_FUNNEL: "repair_whatsapp_followup",
    ActionType.DECREASE_BUDGET: "stabilize_scaling",
    ActionType.INCREASE_BUDGET: "launch_campaign",
    ActionType.DUPLICATE_WINNER: "launch_campaign",
    ActionType.HOLD_STEADY: "hold_steady",
    ActionType.PAUSE_AD: "pause_ad",
}

_FINDING_TO_STAGE = {
    "weak_creative": "creative",
    "creative_fatigue": "creative",
    "poor_followup_downstream": "whatsapp_followup",
    "unstable_scaling": "scaling",
    "low_intent_traffic": "offer",
    "audience_mismatch": "distribution",
    "over_fragmentation": "distribution",
    "insufficient_data": "data",
}

_FINDING_TO_BOTTLENECK = {
    "weak_creative": "low_initial_interest",
    "creative_fatigue": "creative_fatigue",
    "poor_followup_downstream": "lead_ghosting",
    "unstable_scaling": "budget_scaled_without_sales",
    "low_intent_traffic": "weak_offer_after_click",
    "audience_mismatch": "audience_too_narrow",
    "over_fragmentation": "over_fragmentation",
    "insufficient_data": "insufficient_data",
}


def _diagnose_meta_ads(context: Dict[str, Any], risk_score: float) -> Dict[str, Any]:
    report = analyze_context_snapshot(context, risk_score=risk_score)
    entity_report = report.entity_reports[0] if report.entity_reports else None
    findings = list(entity_report.findings) if entity_report else []
    actions = list(entity_report.actions) if entity_report else []
    top_finding = findings[0] if findings else None
    top_action = actions[0] if actions else None
    metrics = entity_report.entity.metrics if entity_report else None

    primary_stage = _FINDING_TO_STAGE.get(getattr(top_finding, "code", ""), "growth")
    strategy = _ACTION_TO_STRATEGY.get(getattr(top_action, "action_type", None), "launch_campaign")
    bottlenecks = [
        _FINDING_TO_BOTTLENECK.get(item.code, item.code)
        for item in findings
    ]
    recommendations = list(entity_report.next_steps) if entity_report else []
    if top_action and top_action.reason not in recommendations:
        recommendations.insert(0, top_action.reason)
    left_on_read_pct = _as_float(
        context.get("left_on_read_pct")
        or context.get("seen_no_reply_pct")
        or context.get("ghosting_pct")
    )
    reply_rate = _as_float(context.get("reply_rate") or context.get("response_rate"))
    first_response_minutes = _as_float(context.get("first_response_minutes"))
    budget_scale_pct = _as_float(context.get("budget_scale_pct") or context.get("spend_change_pct"))
    sales_change_pct = _as_float(context.get("sales_change_pct") or context.get("conversion_change_pct"))
    daily_spend = _as_float(context.get("daily_spend_usd") or context.get("budget_usd") or context.get("spend_usd"))

    if strategy == "hold_steady":
        if left_on_read_pct >= 60 or (reply_rate and reply_rate < 0.25) or first_response_minutes >= 10:
            primary_stage = "whatsapp_followup"
            strategy = "repair_whatsapp_followup"
            if "lead_ghosting" not in bottlenecks:
                bottlenecks.append("lead_ghosting")
            if not recommendations:
                recommendations.append("Revisar primer mensaje, tiempo de respuesta y secuencia de rescate en WhatsApp.")
        elif budget_scale_pct >= 20 and sales_change_pct <= 5 and daily_spend > 0:
            primary_stage = "scaling"
            strategy = "stabilize_scaling"
            if "budget_scaled_without_sales" not in bottlenecks:
                bottlenecks.append("budget_scaled_without_sales")
            if not recommendations:
                recommendations.append("Congelar escalado y validar calidad de lead antes de invertir mas.")

    if risk_score >= 0.85 and "high_risk_account" not in bottlenecks:
        bottlenecks.append("high_risk_account")
        recommendations.append("Evitar cambios bruscos; priorizar estabilidad de la cuenta y feedback de conversion.")

    return {
        "primary_stage": primary_stage,
        "strategy": strategy,
        "bottlenecks": bottlenecks,
        "recommendations": recommendations[:4],
        "meta_snapshot": {
            "ctr": _as_float(getattr(metrics, "ctr", 0.0)),
            "link_ctr": _as_float(getattr(metrics, "link_ctr", 0.0)),
            "roas": _as_float(getattr(metrics, "roas", 0.0)),
            "cpa": _as_float(context.get("cpa") or context.get("cost_per_acquisition")),
            "daily_spend_usd": _as_float(getattr(metrics, "spend", 0.0) or context.get("daily_spend_usd") or context.get("budget_usd")),
            "reply_rate": _as_float(getattr(metrics, "reply_rate", 0.0)),
            "booked_rate": _as_float(getattr(metrics, "booked_rate", 0.0)),
            "left_on_read_pct": _as_float(getattr(metrics, "left_on_read_pct", 0.0)),
            "budget_scale_pct": _as_float(getattr(metrics, "budget_change_pct", 0.0)),
            "sales_change_pct": _as_float(getattr(metrics, "sales_change_pct", 0.0)),
        },
        "operator_summary": report.executive_summary,
        "operator_flags": list(report.flags),
        "proposed_actions": [
            {
                "action_type": item.action_type.value,
                "confidence": item.confidence,
                "risk_score": item.risk_score,
                "reason": item.reason,
                "dry_run": item.dry_run,
                "params": dict(item.params),
            }
            for item in actions
        ],
    }


async def _heuristic_marketing_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
    skill_summary: str,
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    diagnosis = _diagnose_meta_ads(context, risk_score)
    creative = _creative_stack(
        goal=str(handoff.get("goal") or ""),
        context=context,
        campaign_id=str(context.get("campaign_id") or "").strip() or None,
    )
    strategy = str(diagnosis["strategy"])

    tool_result = await execute_tool(
        "execute_case_action",
        args={
            "step": {
                "recommended_action": "execute_campaign",
                "recommended_tool": "internal_service",
                "fallback_tools": ["http", "n8n"],
                "execution_payload": {
                    "source": "marketing_agent",
                    "strategy": strategy,
                    "diagnosis": diagnosis,
                    **creative,
                    "action": {"action": "execute_campaign", "arguments": context},
                },
            }
        },
        session=session,
        organization_id=organization_id,
    )
    return {
        "agent": "marketing_agent",
        "model": AGENT_MODEL,
        "thinking_budget": THINKING_BUDGET,
        "skill": PRIMARY_SKILL_NAME,
        "skill_summary": skill_summary,
        "status": "completed" if bool(tool_result.get("completed")) else "waiting",
        "actions_taken": [strategy, "tool:execute_case_action"],
        "compensation_attempted": False,
        "tool_result": tool_result,
        "selected_tool": str(tool_result.get("selected_tool") or "execute_case_action"),
        "meta_ads_diagnosis": diagnosis,
        **creative,
        "bottlenecks": list(diagnosis.get("bottlenecks") or []),
        "recommendations": list(diagnosis.get("recommendations") or []),
    }


async def run_marketing_agent(
    session: AsyncSession,
    *,
    organization_id: UUID,
    handoff: Dict[str, Any],
) -> Dict[str, Any]:
    context = dict(handoff.get("context") or {})
    risk_score = float(handoff.get("risk_score") or 0.0)
    diagnosis = _diagnose_meta_ads(context, risk_score)
    creative = _creative_stack(
        goal=str(handoff.get("goal") or ""),
        context=context,
        campaign_id=str(context.get("campaign_id") or "").strip() or None,
    )
    skill_summary = _load_skill_summary()
    should_use_agentic = anthropic_agentic_enabled() and risk_score >= 0.7
    if should_use_agentic:
        runtime = AnthropicAgentRuntime()
        try:
            prompt = (
                "Resuelve un caso de campaign execution para Meta Ads con foco en rentabilidad. "
                "Diagnostica si el cuello esta en creativo, oferta, seguimiento por WhatsApp o escalado. "
                "Decide entre refresh_creative, tighten_offer, repair_whatsapp_followup, stabilize_scaling, "
                "optimize_targeting, qualify_leads_harder o launch_campaign. "
                "Usa solo herramientas permitidas y no invoques otros agentes.\n"
                f"Goal: {handoff.get('goal')}\n"
                f"Risk: {handoff.get('risk_score')}\n"
                f"Skill: {skill_summary}\n"
                f"Current diagnosis: {diagnosis}\n"
                f"Offer brief: {creative['offer_brief']}\n"
                f"Creative plan: {creative['creative_plan']}\n"
                f"Creative critic: {creative['creative_review']}\n"
                f"Creative memory: {creative['creative_memory']}\n"
                f"Context: {context}\n"
            )
            agentic = await runtime.run_tool_loop(
                system_prompt=(
                    "Eres marketing_agent. Operas Meta Ads con criterio de rentabilidad. "
                    "Tu prioridad es detectar por que no convierten: creativo, oferta, respuesta por WhatsApp, "
                    "calidad del lead o escalado prematuro. Decide y ejecuta dentro de marketing sin invocar otros agentes."
                ),
                user_message=prompt,
                domain="campaign_execution",
                session=session,
                organization_id=organization_id,
                tool_names=list(handoff.get("allowed_tools") or []),
                max_rounds=2,
                thinking_budget_override=THINKING_BUDGET,
            )
            executed_tools = list(agentic.get("executed_tools") or [])
            if executed_tools:
                last = dict(executed_tools[-1] or {})
                tool_name = str(last.get("tool") or "unknown")
                tool_result = dict(last.get("result") or {})
                return {
                    "agent": "marketing_agent",
                    "model": AGENT_MODEL,
                    "thinking_budget": THINKING_BUDGET,
                    "skill": PRIMARY_SKILL_NAME,
                    "skill_summary": skill_summary,
                    "status": "completed" if bool(tool_result.get("completed")) else "waiting",
                    "actions_taken": ["agentic_marketing_strategy", f"tool:{tool_name}"],
                    "compensation_attempted": False,
                    "tool_result": tool_result,
                    "selected_tool": tool_name,
                    "agentic_note": str(agentic.get("final_answer") or ""),
                    "usage": dict(agentic.get("usage") or {}),
                    "meta_ads_diagnosis": diagnosis,
                    **creative,
                    "bottlenecks": list(diagnosis.get("bottlenecks") or []),
                    "recommendations": list(diagnosis.get("recommendations") or []),
                }
        except Exception:
            if os.getenv("MARKETING_AGENT_TRACEBACK", "false").lower() in {"1", "true", "yes"}:
                print("MARKETING_AGENT_TRACEBACK_START")
                print(traceback.format_exc())
                print("MARKETING_AGENT_TRACEBACK_END")
    return await _heuristic_marketing_agent(
        session,
        organization_id=organization_id,
        handoff=handoff,
        skill_summary=skill_summary,
    )
