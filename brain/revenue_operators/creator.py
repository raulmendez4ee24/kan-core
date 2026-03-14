from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from brain.agents.creative_critic_agent import critique_creative_plan
from brain.agents.creative_strategist_agent import build_creative_plan
from brain.agents.offer_analyzer_agent import analyze_offer
from brain.meta_ads_operator import analyze_context_snapshot


class CreatorAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action_type: str
    confidence: float
    risk_score: float
    reason: str
    next_step: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CreatorOperator:
    async def analyze_creative_engine(self, *, goal: str, context: dict[str, Any]) -> dict[str, Any]:
        offer_brief = analyze_offer(goal=goal, context=context)
        creative_plan = build_creative_plan(offer_brief=offer_brief, context=context)
        creative_review = critique_creative_plan(offer_brief=offer_brief, creative_plan=creative_plan)
        operator_report = analyze_context_snapshot(context, risk_score=float(context.get("risk_score") or 0.0))
        return {
            "offer_brief": offer_brief,
            "creative_plan": creative_plan,
            "creative_review": creative_review,
            "operator_report": asdict(operator_report) if is_dataclass(operator_report) else operator_report,
        }

    async def suggest_actions(self, *, goal: str, context: dict[str, Any]) -> list[CreatorAction]:
        analysis = await self.analyze_creative_engine(goal=goal, context=context)
        review = dict(analysis["creative_review"])
        winner = dict(review.get("winner") or {})
        findings = list((analysis["operator_report"] or {}).get("flags") or [])
        actions: list[CreatorAction] = []

        if "creative_fatigue" in findings:
            actions.append(
                CreatorAction(
                    action_type="mark_for_creative_refresh",
                    confidence=0.84,
                    risk_score=0.35,
                    reason="La campaña muestra señales de fatiga creativa.",
                    next_step="Generar variantes del hook ganador y testear nuevas piezas.",
                    payload={"winner": winner},
                )
            )
        if "poor_followup_downstream" in findings:
            actions.append(
                CreatorAction(
                    action_type="inspect_followup_funnel",
                    confidence=0.78,
                    risk_score=0.25,
                    reason="El tráfico responde, pero el funnel de cierre se debilita después del clic.",
                    next_step="Revisar WhatsApp, tiempos de respuesta y secuencia comercial.",
                    payload={"winner": winner},
                )
            )
        if not actions:
            actions.append(
                CreatorAction(
                    action_type="hold_steady",
                    confidence=0.67,
                    risk_score=0.10,
                    reason="No hay suficiente evidencia para tocar campañas fuertes hoy.",
                    next_step="Mantener estabilidad y seguir observando resultados del creativo ganador.",
                    payload={"winner": winner},
                )
            )
        return actions
