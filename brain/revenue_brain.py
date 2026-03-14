from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from brain.agents.agent_governor import evaluate_handoff
from brain.forge import OfferForge, save_generated_offer
from brain.market_scanner import MarketScanner
from brain.meta_ads_autonomy import MetaAdsAnalyzer, MetaAdsExecutor, generate_and_store_meta_ads_dry_run
from brain.product_discovery import ProductDiscovery
from brain.revenue_operators import (
    CloserOperator,
    CreatorOperator,
    FarmerOperator,
)
from brain.revenue_operators.hunter import OpportunityInput

AUTOMATION_THRESHOLD = float(os.getenv("REVENUE_AUTOMATION_THRESHOLD") or 0.82)


class RevenueAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operator: str
    action_type: str
    priority: int
    confidence: float
    channel: str
    budget_mxn: float = 0.0
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RevenueDiagnosis(BaseModel):
    generated_at: str
    summary: str
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class DailyBriefing(BaseModel):
    generated_at: str
    diagnosis: RevenueDiagnosis
    actions: list[RevenueAction] = Field(default_factory=list)
    executed: list[RevenueAction] = Field(default_factory=list)
    recommended: list[RevenueAction] = Field(default_factory=list)


class RevenueBrain:
    def __init__(
        self,
        *,
        voice_say: Any | None = None,    # _SayFn — injectable for tests / production wiring
        voice_listen: Any | None = None, # _ListenFn — injectable for tests / production wiring
    ) -> None:
        self.hunter = MarketScanner()
        self.closer = CloserOperator()
        self.farmer = FarmerOperator()
        self.creator = CreatorOperator()
        self.offer_forge = OfferForge()
        self.meta_ads_analyzer = MetaAdsAnalyzer()
        self.meta_ads_executor = MetaAdsExecutor()
        self.product_discovery = ProductDiscovery()
        self._voice_say = voice_say
        self._voice_listen = voice_listen

    async def gather_metrics(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        metrics = dict(snapshot or {})
        city = str(metrics.get("city") or "Guadalajara")
        if "job_board_results" not in metrics:
            metrics["job_board_results"] = await self.hunter.scan_job_boards(city=city)
        if "competitor_activity_results" not in metrics:
            competitor_domains = list(metrics.get("competitor_domains") or [])
            metrics["competitor_activity_results"] = (
                await self.hunter.scan_competitor_activity(competitor_domains)
                if competitor_domains
                else []
            )
        if "complaints_results" not in metrics:
            metrics["complaints_results"] = await self.hunter.scan_complaints()
        if "arbitrage_results" not in metrics:
            metrics["arbitrage_results"] = await self.hunter.scan_arbitrage()
        if "internal_demand_results" not in metrics:
            metrics["internal_demand_results"] = await self.hunter.scan_internal_demand(
                crm_texts=list(metrics.get("crm_signal_texts") or []),
                conversation_texts=list(metrics.get("conversation_signal_texts") or []),
            )
        if "meta_ads_report" not in metrics:
            try:
                meta_snapshot = metrics.get("meta_ads_snapshot")
                report = await self.meta_ads_analyzer.daily_report(snapshot=meta_snapshot)
                metrics["meta_ads_report"] = report.model_dump(mode="json")
                dry_run = await generate_and_store_meta_ads_dry_run(report)
                metrics["meta_ads_dry_run"] = dry_run.model_dump(mode="json")
                account_flags = list(report.account_flags)
                if account_flags:
                    existing_flags = list(metrics.get("campaign_flags") or [])
                    metrics["campaign_flags"] = list(dict.fromkeys([*existing_flags, *account_flags]))
            except Exception:
                # Meta Ads should never block the daily cycle if API credentials or network are unavailable.
                pass
        return metrics

    async def diagnose(self, metrics: dict[str, Any]) -> RevenueDiagnosis:
        revenue_yesterday = float(metrics.get("revenue_yesterday_mxn") or 0.0)
        active_leads = int(metrics.get("active_leads") or 0)
        churn_risk = int(metrics.get("churn_risk_customers") or 0)
        ad_flags = list(metrics.get("campaign_flags") or [])
        opportunities: list[str] = []
        risks: list[str] = []

        if active_leads:
            opportunities.append(f"Hay {active_leads} leads activos que pueden convertirse hoy.")
        if revenue_yesterday <= 0:
            risks.append("Ayer no se registró revenue; conviene revisar pipeline y cierres.")
        if churn_risk:
            risks.append(f"Hay {churn_risk} clientes con riesgo de churn.")
        if ad_flags:
            opportunities.append(f"Hay hallazgos de campañas: {', '.join(ad_flags[:3])}.")

        summary = (
            "Yo voy a priorizar captación, cierre y retención según las señales más fuertes de hoy."
        )
        return RevenueDiagnosis(
            generated_at=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            opportunities=opportunities,
            risks=risks,
            metrics=metrics,
        )

    async def decide_actions(self, diagnosis: RevenueDiagnosis) -> list[RevenueAction]:
        metrics = diagnosis.metrics
        actions: list[RevenueAction] = []

        hunt_report = await self.hunter.daily_hunt(
            google_maps_results=list(metrics.get("google_maps_results") or []),
            instagram_results=list(metrics.get("instagram_results") or []),
            trends_results=list(metrics.get("trends_results") or []),
            job_board_results=list(metrics.get("job_board_results") or []),
            competitor_activity_results=list(metrics.get("competitor_activity_results") or []),
            complaints_results=list(metrics.get("complaints_results") or []),
            arbitrage_results=list(metrics.get("arbitrage_results") or []),
            internal_demand_results=list(metrics.get("internal_demand_results") or []),
            business_types=list(metrics.get("business_types") or []),
            hashtags=list(metrics.get("hashtags") or []),
            trend_keywords=list(metrics.get("trend_keywords") or []),
            city=str(metrics.get("city") or "Guadalajara"),
            products=list(metrics.get("products") or []),
            conversations=list(metrics.get("conversations") or []),
            lost_deals=list(metrics.get("lost_deals") or []),
        )
        metrics["hunt_report"] = hunt_report.model_dump(mode="json")
        if hunt_report.top_opportunities:
            top = hunt_report.top_opportunities[0]
            actions.append(
                RevenueAction(
                    operator="hunter",
                    action_type="prepare_outreach",
                    priority=75,
                    confidence=min(0.92, top.score / 100.0),
                    channel="whatsapp",
                    reason=f"Oportunidad prioritaria detectada: {top.business_name}",
                    payload=top.model_dump(),
                )
            )

        closer_actions = await self.closer.daily_close_plan(list(metrics.get("pipeline_leads") or []))
        for item in closer_actions[:3]:
            actions.append(
                RevenueAction(
                    operator="closer",
                    action_type=item.action_type,
                    priority=item.priority,
                    confidence=item.confidence,
                    channel=item.channel,
                    reason=item.reason,
                    payload=item.model_dump(),
                )
            )

        farmer_actions = await self.farmer.daily_farm(list(metrics.get("customers") or []))
        for item in farmer_actions[:3]:
            actions.append(
                RevenueAction(
                    operator="farmer",
                    action_type=item.action_type,
                    priority=68 if item.action_type == "upsell" else 62,
                    confidence=item.confidence,
                    channel="whatsapp",
                    reason=item.reason,
                    payload=item.model_dump(),
                )
            )

        creator_actions = await self.creator.suggest_actions(
            goal=str(metrics.get("campaign_goal") or "generar ventas para agentes y automatización"),
            context=dict(metrics.get("campaign_context") or {}),
        )
        for item in creator_actions[:2]:
            actions.append(
                RevenueAction(
                    operator="creator",
                    action_type=item.action_type,
                    priority=72,
                    confidence=item.confidence,
                    channel="meta_ads",
                    reason=item.reason,
                    payload=item.model_dump(),
                )
            )

        actions.sort(key=lambda item: (item.priority, item.confidence), reverse=True)
        return actions

    async def execute(self, action: RevenueAction) -> RevenueAction:
        return action

    async def recommend_to_raul(self, action: RevenueAction) -> RevenueAction:
        return action

    async def send_daily_briefing(
        self,
        *,
        diagnosis: RevenueDiagnosis,
        actions: list[RevenueAction],
        executed: list[RevenueAction],
        recommended: list[RevenueAction],
    ) -> DailyBriefing:
        return DailyBriefing(
            generated_at=datetime.now(timezone.utc).isoformat(),
            diagnosis=diagnosis,
            actions=actions,
            executed=executed,
            recommended=recommended,
        )

    def _opportunity_score_to_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        return {
            "business_name": str(payload.get("business_name") or "Unknown opportunity"),
            "vertical": str(payload.get("vertical") or "services"),
            "city": str(payload.get("city") or "Guadalajara"),
            "estimated_market_size": int(metadata.get("estimated_market_size") or round(float(payload.get("market_score") or 0.0) * 10)),
            "pain_score": round(float(payload.get("pain_score") or 0.0) / 100.0, 4),
            "payment_capacity_score": round(float(payload.get("payment_capacity_score") or 0.0) / 100.0, 4),
            "competition_score": round(float(payload.get("competition_score") or 0.0) / 100.0, 4),
            "stack_fit_score": round(float(payload.get("stack_fit_score") or 0.0) / 100.0, 4),
            "source": str(metadata.get("source") or "hunter"),
            "metadata": metadata,
        }

    async def _forge_hunter_assets(self, top_opportunities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        generated_assets: list[dict[str, Any]] = []
        for payload in top_opportunities:
            try:
                opportunity = self._opportunity_score_to_input(payload)
                complete_offer = await self.offer_forge.forge_offer(
                    OpportunityInput.model_validate(opportunity)
                )
                landing_html = None
                ad_creatives = []
                score = float(payload.get("score") or 0.0)
                if score > 85:
                    landing_html = self.offer_forge.generate_landing_page(complete_offer)
                    ad_creatives = self.offer_forge.generate_ad_creatives(complete_offer)
                stored = save_generated_offer(
                    complete_offer,
                    landing_html=landing_html,
                    ad_creatives=ad_creatives,
                )
                generated_assets.append(stored.model_dump(mode="json"))
            except Exception:
                continue
        return generated_assets

    async def evening_check(self, snapshot: dict[str, Any] | None = None) -> DailyBriefing:
        try:
            await self.meta_ads_executor.check_rollback()
        except Exception:
            pass  # rollback check must never block the evening cycle
        metrics = await self.gather_metrics(snapshot)
        diagnosis = await self.diagnose(metrics)
        actions: list[RevenueAction] = []

        closer_actions = await self.closer.daily_close_plan(list(metrics.get("pipeline_leads") or []))
        for item in closer_actions[:5]:
            actions.append(
                RevenueAction(
                    operator="closer",
                    action_type=item.action_type,
                    priority=item.priority,
                    confidence=item.confidence,
                    channel=item.channel,
                    reason=item.reason,
                    payload=item.model_dump(),
                )
            )

        farmer_actions = await self.farmer.daily_farm(list(metrics.get("customers") or []))
        for item in farmer_actions[:5]:
            actions.append(
                RevenueAction(
                    operator="farmer",
                    action_type=item.action_type,
                    priority=68 if item.action_type == "upsell" else 62,
                    confidence=item.confidence,
                    channel="whatsapp",
                    reason=item.reason,
                    payload=item.model_dump(),
                )
            )

        actions.sort(key=lambda item: (item.priority, item.confidence), reverse=True)
        executed: list[RevenueAction] = []
        recommended: list[RevenueAction] = []

        for action in actions:
            handoff = {
                "goal": "evening_check",
                "risk_score": 0.3,
                "allowed_tools": ["execute_case_action", "run_rollback"],
                "context": action.payload,
            }
            gate = evaluate_handoff(agent_name=action.operator, handoff=handoff)
            if not gate.get("permitted", True):
                recommended.append(
                    action.model_copy(
                        update={
                            "reason": (
                                f"{action.reason} Bloqueado por governor: "
                                f"{gate.get('hold_reason') or 'policy_block'}"
                            )
                        }
                    )
                )
                continue
            if action.confidence >= AUTOMATION_THRESHOLD:
                executed.append(await self.execute(action))
            else:
                recommended.append(await self.recommend_to_raul(action))

        # --- Voice: followup_call for stale leads (3+ days, phone in metadata) ----
        if self._voice_say and self._voice_listen:
            try:
                await self._trigger_followup_calls(list(metrics.get("pipeline_leads") or []))
            except Exception:
                pass  # voice calls must never block the briefing

        return await self.send_daily_briefing(
            diagnosis=diagnosis,
            actions=actions,
            executed=executed,
            recommended=recommended,
        )

    async def _trigger_followup_calls(self, leads: list[Any]) -> None:
        """Fire followup_call tasks for leads silent for 3+ days (non-blocking)."""
        import asyncio as _asyncio
        from brain.voice_engine import followup_call

        now = datetime.now(timezone.utc)
        stale_limit = 3  # cap concurrent calls per evening cycle

        for lead in leads:
            if stale_limit <= 0:
                break
            phone = str((lead.metadata or {}).get("phone") or "").strip()
            if not phone:
                continue
            last = getattr(lead, "last_contact_at", None)
            if last and (now - last).days >= 3:
                # call_sid is unknown at launch time — production wires this via Twilio
                # Here we pass a placeholder; real wiring uses make_call first
                call_sid = str((lead.metadata or {}).get("call_sid") or "pending")
                _asyncio.create_task(
                    followup_call(
                        lead,
                        call_sid=call_sid,
                        say=self._voice_say,
                        listen=self._voice_listen,
                    )
                )
                stale_limit -= 1

    async def trigger_outreach_calls(
        self,
        opportunities: list[Any],
        offers: list[Any] | None = None,
        *,
        call_sid_map: dict[str, str] | None = None,
    ) -> list[str]:
        """Fire outreach_call for each opportunity with score > 85.

        *call_sid_map* maps opportunity business_name → Twilio call SID (pre-dialled).
        Returns the list of business names for which a call was attempted.
        """
        from brain.voice_engine import outreach_call

        if not self._voice_say or not self._voice_listen:
            return []

        offers_map: dict[str, Any] = {}
        for offer in (offers or []):
            vert = str(getattr(offer, "vertical", "")).strip().lower()
            if vert:
                offers_map[vert] = offer

        attempted: list[str] = []
        for opp in opportunities:
            score = float(getattr(opp, "score", 0.0))
            if score <= 85:
                continue
            biz = str(getattr(opp, "business_name", "")).strip()
            call_sid = str((call_sid_map or {}).get(biz) or "pending")
            offer = offers_map.get(str(getattr(opp, "vertical", "")).strip().lower())
            try:
                import asyncio as _asyncio
                _asyncio.create_task(
                    outreach_call(
                        opp,
                        call_sid=call_sid,
                        say=self._voice_say,
                        listen=self._voice_listen,
                        offer=offer,
                    )
                )
                attempted.append(biz)
            except Exception:
                pass
        return attempted

    async def daily_cycle(self, snapshot: dict[str, Any] | None = None) -> DailyBriefing:
        metrics = await self.gather_metrics(snapshot)
        diagnosis = await self.diagnose(metrics)
        actions = await self.decide_actions(diagnosis)
        hunt_report = dict(diagnosis.metrics.get("hunt_report") or {})
        generated_offers = await self._forge_hunter_assets(list(hunt_report.get("top_opportunities") or []))
        if generated_offers:
            diagnosis.metrics["generated_offers"] = generated_offers
            diagnosis.opportunities.append(
                f"OfferForge generó {len(generated_offers)} ofertas para oportunidades del Hunter."
            )
        executed: list[RevenueAction] = []
        recommended: list[RevenueAction] = []

        for action in actions:
            handoff = {
                "goal": diagnosis.summary,
                "risk_score": 0.9 if action.action_type in {"prepare_outreach", "increase_budget"} else 0.3,
                "allowed_tools": ["execute_case_action", "run_rollback"],
                "context": action.payload,
            }
            gate = evaluate_handoff(agent_name=action.operator, handoff=handoff)
            if not gate.get("permitted", True):
                recommended.append(
                    action.model_copy(
                        update={
                            "reason": (
                                f"{action.reason} Bloqueado por governor: "
                                f"{gate.get('hold_reason') or 'policy_block'}"
                            )
                        }
                    )
                )
                continue
            if action.confidence >= AUTOMATION_THRESHOLD:
                executed.append(await self.execute(action))
            else:
                recommended.append(await self.recommend_to_raul(action))

        proposals = await self.product_discovery.run(snapshot=metrics)
        if proposals:
            diagnosis.opportunities.extend(
                [
                    f"Nuevo servicio detectado: {proposal.service_name} ({proposal.raw_signals_count} senales)"
                    for proposal in proposals
                ]
            )

        # --- Voice: auto-call hunter opportunities with score > 85 ---------------
        if self._voice_say and self._voice_listen:
            try:
                hunter_opps = [
                    action.payload
                    for action in actions
                    if action.operator == "hunter" and action.payload
                ]
                if hunter_opps:
                    # Reconstruct lightweight proxy objects from action payloads
                    class _OppProxy:
                        def __init__(self, d: dict) -> None:
                            self.score = float(d.get("score", 0.0))
                            self.business_name = str(d.get("business_name", ""))
                            self.vertical = str(d.get("vertical", ""))
                            self.city = str(d.get("city", ""))
                            self.reasons = list(d.get("reasons", []))

                    await self.trigger_outreach_calls(
                        [_OppProxy(p) for p in hunter_opps]
                    )
            except Exception:
                pass  # voice calls must never block the daily cycle

        return await self.send_daily_briefing(
            diagnosis=diagnosis,
            actions=actions,
            executed=executed,
            recommended=recommended,
        )
