from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CustomerHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_id: str
    name: str = "Cliente"
    current_products: list[str] = Field(default_factory=list)
    usage_score: float = 0.0
    satisfaction_score: float = 0.0
    nps: int | None = None
    renewal_date: datetime | None = None
    cancelled_at: datetime | None = None
    churn_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FarmerAction(BaseModel):
    customer_id: str
    action_type: str
    confidence: float
    reason: str
    offer: str | None = None


class FarmerOperator:
    async def evaluate_customer(self, customer: CustomerHealth) -> list[FarmerAction]:
        actions: list[FarmerAction] = []
        now = datetime.now(timezone.utc)
        if customer.cancelled_at and customer.cancelled_at <= now - timedelta(days=60):
            actions.append(
                FarmerAction(
                    customer_id=customer.customer_id,
                    action_type="reactivate_churned",
                    confidence=0.72,
                    reason="Cliente churned hace 60+ días; vale la pena un intento de regreso.",
                    offer="oferta_regreso",
                )
            )
            return actions

        if customer.usage_score < 0.35 or customer.satisfaction_score < 0.45:
            actions.append(
                FarmerAction(
                    customer_id=customer.customer_id,
                    action_type="churn_intervention",
                    confidence=0.81,
                    reason="Se detecta uso o satisfacción baja; riesgo de churn alto.",
                )
            )

        products = {item.lower() for item in customer.current_products}
        if "pagina web" in products and "chatbot" not in products:
            actions.append(
                FarmerAction(
                    customer_id=customer.customer_id,
                    action_type="upsell",
                    confidence=0.74,
                    reason="Tiene web, pero todavía no chatbot.",
                    offer="web_plus_chatbot",
                )
            )
        elif "chatbot" in products and "agente de voz" not in products:
            actions.append(
                FarmerAction(
                    customer_id=customer.customer_id,
                    action_type="upsell",
                    confidence=0.70,
                    reason="Tiene chatbot y ya puede crecer a agente de voz.",
                    offer="chatbot_plus_voice",
                )
            )

        if (customer.nps or 0) >= 9:
            actions.append(
                FarmerAction(
                    customer_id=customer.customer_id,
                    action_type="ask_referral",
                    confidence=0.77,
                    reason="Cliente promotor; conviene pedir referido.",
                    offer="referral_request",
                )
            )
        return actions

    async def daily_farm(self, customers: list[CustomerHealth]) -> list[FarmerAction]:
        actions: list[FarmerAction] = []
        for customer in customers:
            actions.extend(await self.evaluate_customer(customer))
        return actions
