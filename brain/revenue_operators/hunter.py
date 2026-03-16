from __future__ import annotations

from collections import Counter
import hashlib
import os
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tools.lead_generator import search_places


class OpportunityInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    business_name: str
    vertical: str
    city: str = "Guadalajara"
    has_website: bool = True
    has_bot: bool = False
    engagement_score: float = 0.0
    trend_score: float = 0.0
    estimated_market_size: int = 0
    pain_score: float = 0.0
    payment_capacity_score: float = 0.0
    competition_score: float = 0.0
    stack_fit_score: float = 0.0
    source: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityScore(BaseModel):
    business_name: str
    vertical: str
    city: str
    score: float
    market_score: float
    pain_score: float
    payment_capacity_score: float
    competition_score: float
    stack_fit_score: float
    should_contact: bool = False
    reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Offer(BaseModel):
    vertical: str
    package_name: str
    setup_price_mxn: int
    monthly_price_mxn: int
    summary: str
    value_points: list[str] = Field(default_factory=list)


class OutreachMessage(BaseModel):
    channel: str
    subject: str
    body: str
    cta: str


class ProductIdea(BaseModel):
    product_name: str
    evidence_count: int
    estimated_monthly_revenue_mxn: int
    rationale: str


class HuntReport(BaseModel):
    opportunities_scanned: int
    top_opportunities: list[OpportunityScore] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    outreach_messages: list[OutreachMessage] = Field(default_factory=list)
    product_gaps: list[ProductIdea] = Field(default_factory=list)


def _bounded(value: float, *, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


_VERTICAL_DEFAULTS: dict[str, dict[str, float | int]] = {
    "dentists": {
        "estimated_market_size": 240,
        "pain_score": 0.8,
        "payment_capacity_score": 0.84,
        "competition_score": 0.44,
        "stack_fit_score": 0.9,
    },
    "clinics": {
        "estimated_market_size": 180,
        "pain_score": 0.86,
        "payment_capacity_score": 0.88,
        "competition_score": 0.46,
        "stack_fit_score": 0.95,
    },
    "spas": {
        "estimated_market_size": 220,
        "pain_score": 0.74,
        "payment_capacity_score": 0.76,
        "competition_score": 0.32,
        "stack_fit_score": 0.84,
    },
    "barbershops": {
        "estimated_market_size": 320,
        "pain_score": 0.68,
        "payment_capacity_score": 0.64,
        "competition_score": 0.28,
        "stack_fit_score": 0.8,
    },
    "restaurants": {
        "estimated_market_size": 480,
        "pain_score": 0.78,
        "payment_capacity_score": 0.7,
        "competition_score": 0.34,
        "stack_fit_score": 0.89,
    },
}


def _infer_city(default_city: str, address: str | None) -> str:
    address_text = (address or "").lower()
    if "zapopan" in address_text:
        return "Zapopan"
    if "guadalajara" in address_text:
        return "Guadalajara"
    return default_city


def _normalize_vertical(raw_vertical: str) -> str:
    value = raw_vertical.strip().lower()
    if value in {"dentist", "dentists", "dental", "dentistas"}:
        return "dentists"
    if value in {"clinic", "clinics", "clinicas", "clinica", "medical clinic"}:
        return "clinics"
    if value in {"spa", "spas"}:
        return "spas"
    if value in {"barber", "barbershop", "barbershops", "barber shop", "barber shops"}:
        return "barbershops"
    if value in {"restaurant", "restaurants", "restaurante", "restaurantes"}:
        return "restaurants"
    return value


def _tie_break_noise(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return round((bucket * 4.0) - 2.0, 2)


def _build_place_opportunity(default_city: str, vertical: str, row: dict[str, Any]) -> OpportunityInput:
    normalized_vertical = _normalize_vertical(vertical)
    defaults = dict(_VERTICAL_DEFAULTS.get(normalized_vertical, {}))
    website = bool(row.get("website"))
    rating_count = int(row.get("user_ratings_total") or 0)
    rating = float(row.get("rating") or 0.0)
    competition_score = float(defaults.get("competition_score", 0.35))
    if rating_count >= 500:
        competition_score += 0.15
    elif rating_count >= 200:
        competition_score += 0.08
    elif rating_count <= 25:
        competition_score -= 0.05
    if rating >= 4.7 and rating_count >= 100:
        competition_score += 0.04

    pain_score = float(defaults.get("pain_score", 0.65))
    stack_fit_score = float(defaults.get("stack_fit_score", 0.75))
    if not website:
        pain_score += 0.08
        stack_fit_score += 0.06

    return OpportunityInput(
        business_name=str(row.get("name") or "Unknown business"),
        vertical=normalized_vertical,
        city=_infer_city(default_city, row.get("address")),
        has_website=website,
        has_bot=False,
        estimated_market_size=int(defaults.get("estimated_market_size", 180)),
        pain_score=min(pain_score, 0.98),
        payment_capacity_score=float(defaults.get("payment_capacity_score", 0.65)),
        competition_score=max(0.05, min(competition_score, 0.9)),
        stack_fit_score=min(stack_fit_score, 0.98),
        source="google_maps",
        metadata=dict(row),
    )


class HunterOperator:
    def _coerce_opportunity(
        self,
        row: OpportunityInput | dict[str, Any],
        *,
        source_hint: str,
    ) -> OpportunityInput:
        if isinstance(row, OpportunityInput):
            opportunity = row.model_copy(deep=True)
            if source_hint and not opportunity.source:
                opportunity.source = source_hint
            return opportunity
        payload = dict(row)
        if source_hint and not payload.get("source"):
            payload["source"] = source_hint
        return OpportunityInput(**payload)

    async def scan_google_maps(
        self,
        city: str,
        business_types: Iterable[str],
        radius: int,
        *,
        results: Iterable[dict[str, Any]] | None = None,
    ) -> list[OpportunityInput]:
        del radius
        if results is None:
            api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
            places_rows: list[dict[str, Any]] = []
            seen_place_ids: set[str] = set()
            search_cities = [city]
            if city.lower() == "guadalajara":
                search_cities.append("Zapopan")
            for business_type in business_types:
                for search_city in search_cities:
                    found = await search_places(
                        business_type,
                        f"{search_city}, Jalisco, Mexico",
                        api_key,
                        max_results=20,
                    )
                    for row in found:
                        place_id = str(row.get("place_id") or "")
                        if place_id and place_id in seen_place_ids:
                            continue
                        if place_id:
                            seen_place_ids.add(place_id)
                        places_rows.append({"business_type": business_type, **row})
            results = places_rows
        inputs = []
        for row in results or []:
            vertical = str(row.get("vertical") or row.get("business_type") or next(iter(business_types), "general"))
            if row.get("name") and "estimated_market_size" not in row:
                inputs.append(_build_place_opportunity(city, vertical, row))
                continue
            inputs.append(
                OpportunityInput(
                    business_name=str(row.get("business_name") or row.get("name") or "Unknown business"),
                    vertical=vertical,
                    city=city,
                    has_website=bool(row.get("has_website", True)),
                    has_bot=bool(row.get("has_bot", False)),
                    estimated_market_size=int(row.get("estimated_market_size") or 0),
                    pain_score=float(row.get("pain_score") or 0.0),
                    payment_capacity_score=float(row.get("payment_capacity_score") or 0.0),
                    competition_score=float(row.get("competition_score") or 0.0),
                    stack_fit_score=float(row.get("stack_fit_score") or 0.0),
                    source="google_maps",
                    metadata=dict(row),
                )
            )
        return inputs

    async def scan_instagram(
        self,
        hashtags: Iterable[str],
        location: str,
        *,
        results: Iterable[dict[str, Any]] | None = None,
    ) -> list[OpportunityInput]:
        del hashtags
        inputs = []
        for row in results or []:
            inputs.append(
                OpportunityInput(
                    business_name=str(row.get("business_name") or row.get("handle") or "Unknown IG business"),
                    vertical=str(row.get("vertical") or "general"),
                    city=location,
                    has_website=bool(row.get("has_website", True)),
                    has_bot=bool(row.get("has_bot", False)),
                    engagement_score=float(row.get("engagement_score") or 0.0),
                    trend_score=float(row.get("trend_score") or 0.0),
                    pain_score=float(row.get("pain_score") or 0.0),
                    payment_capacity_score=float(row.get("payment_capacity_score") or 0.0),
                    competition_score=float(row.get("competition_score") or 0.0),
                    stack_fit_score=float(row.get("stack_fit_score") or 0.0),
                    source="instagram",
                    metadata=dict(row),
                )
            )
        return inputs

    async def scan_trends(
        self,
        keywords: Iterable[str],
        region: str,
        *,
        results: Iterable[dict[str, Any]] | None = None,
    ) -> list[OpportunityInput]:
        inputs = []
        for row in results or []:
            inputs.append(
                OpportunityInput(
                    business_name=str(row.get("business_name") or f"trend:{row.get('keyword') or next(iter(keywords), 'unknown')}"),
                    vertical=str(row.get("vertical") or "general"),
                    city=region,
                    trend_score=float(row.get("trend_score") or 0.0),
                    estimated_market_size=int(row.get("estimated_market_size") or 0),
                    pain_score=float(row.get("pain_score") or 0.0),
                    payment_capacity_score=float(row.get("payment_capacity_score") or 0.0),
                    competition_score=float(row.get("competition_score") or 0.0),
                    stack_fit_score=float(row.get("stack_fit_score") or 0.0),
                    source="trends",
                    metadata=dict(row),
                )
            )
        return inputs

    async def evaluate_opportunity(self, business_data: OpportunityInput) -> OpportunityScore:
        market_score = _bounded(float(business_data.estimated_market_size) / 10.0)
        pain_score = _bounded(float(business_data.pain_score) * 100 if business_data.pain_score <= 1 else float(business_data.pain_score))
        payment_score = _bounded(float(business_data.payment_capacity_score) * 100 if business_data.payment_capacity_score <= 1 else float(business_data.payment_capacity_score))
        competition_penalty = _bounded(float(business_data.competition_score) * 100 if business_data.competition_score <= 1 else float(business_data.competition_score))
        stack_fit_score = _bounded(float(business_data.stack_fit_score) * 100 if business_data.stack_fit_score <= 1 else float(business_data.stack_fit_score))
        rating = float((business_data.metadata or {}).get("rating") or 0.0)
        rating_count = int((business_data.metadata or {}).get("user_ratings_total") or 0)
        review_signal = _bounded((rating * 12.0) + min(rating_count / 12.0, 35.0))
        website_penalty = 0.0 if business_data.has_website else 10.0
        bot_penalty = 0.0 if business_data.has_bot else 7.0

        score = (
            market_score * 0.12
            + pain_score * 0.28
            + payment_score * 0.20
            + stack_fit_score * 0.18
            + review_signal * 0.10
            + _bounded(float(business_data.engagement_score)) * 0.04
            + _bounded(float(business_data.trend_score)) * 0.08
            - competition_penalty * 0.12
            + website_penalty
            + bot_penalty
        )
        score += _tie_break_noise(
            f"{business_data.vertical}:{business_data.business_name}:{(business_data.metadata or {}).get('place_id') or ''}"
        )
        score = _bounded(score)

        reasons: list[str] = []
        if not business_data.has_website:
            reasons.append("No tiene sitio web visible.")
        if not business_data.has_bot:
            reasons.append("No tiene bot o automatización visible.")
        if pain_score >= 70:
            reasons.append("Se detecta dolor comercial alto.")
        if stack_fit_score >= 75:
            reasons.append("Encaja muy bien con el stack actual.")

        return OpportunityScore(
            business_name=business_data.business_name,
            vertical=business_data.vertical,
            city=business_data.city,
            score=round(score, 2),
            market_score=round(market_score, 2),
            pain_score=round(pain_score, 2),
            payment_capacity_score=round(payment_score, 2),
            competition_score=round(competition_penalty, 2),
            stack_fit_score=round(stack_fit_score, 2),
            should_contact=score >= 85,
            reasons=reasons,
            metadata=dict(business_data.metadata),
        )

    async def generate_offer(self, opportunity: OpportunityScore, products: Iterable[str]) -> Offer:
        available = {str(item).lower() for item in products}
        if "paginas web personalizadas" in available and "chatbots" in available:
            package_name = "Web + Chatbot"
            setup_price_mxn = 7500
            monthly_price_mxn = 1200
            value_points = [
                "sitio optimizado para captar leads",
                "chatbot en WhatsApp para responder 24/7",
                "seguimiento inicial de prospectos",
            ]
        elif "chatbots" in available:
            package_name = "Chatbot WhatsApp"
            setup_price_mxn = 3500
            monthly_price_mxn = 800
            value_points = [
                "respuesta automática 24/7",
                "catálogo o preguntas frecuentes",
                "captura de leads",
            ]
        else:
            package_name = "Automatización Inicial"
            setup_price_mxn = 5000
            monthly_price_mxn = 1000
            value_points = [
                "automatización de un proceso comercial",
                "seguimiento básico de leads",
                "operación más rápida sin contratar más personal",
            ]

        summary = (
            f"Oferta enfocada a {opportunity.vertical}: {package_name} "
            f"para captar y atender prospectos con un setup desde ${setup_price_mxn:,} MXN."
        )
        return Offer(
            vertical=opportunity.vertical,
            package_name=package_name,
            setup_price_mxn=setup_price_mxn,
            monthly_price_mxn=monthly_price_mxn,
            summary=summary,
            value_points=value_points,
        )

    async def _generate_outreach_body(
        self,
        opportunity: OpportunityScore,
        offer: Offer,
        channel: str,
    ) -> str:
        from brain.marketing_skills import load_skill

        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        skill_context = load_skill("cold_email")
        system_prompt = (
            "Eres un especialista en outreach B2B para una agencia de automatizacion y chatbots. "
            "Escribe mensajes de prospección cortos, personalizados y orientados a generar respuesta. "
            "Responde SOLO con el cuerpo del mensaje, sin asunto ni firma."
            + (f"\n\n{skill_context}" if skill_context else "")
        )
        user_msg = (
            f"Canal: {channel}\n"
            f"Negocio: {opportunity.business_name}\n"
            f"Vertical: {opportunity.vertical}\n"
            f"Oferta: {offer.package_name} (desde ${offer.setup_price_mxn:,} MXN setup)\n"
            f"Puntos de valor: {', '.join(offer.value_points[:3])}\n\n"
            "Escribe el mensaje de outreach."
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                    "max_tokens": 400,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            response.raise_for_status()
            blocks = response.json().get("content") or []
            return "\n".join(
                str(b.get("text") or "").strip() for b in blocks if isinstance(b, dict)
            ).strip()

    async def generate_outreach(
        self,
        opportunity: OpportunityScore,
        offer: Offer,
        channel: str,
    ) -> OutreachMessage:
        subject = f"{offer.package_name} para {opportunity.business_name}"
        body = (
            f"Hola, revisé {opportunity.business_name} y veo una oportunidad clara para "
            f"captar y atender mejor prospectos. Yo preparé una propuesta breve de {offer.package_name} "
            f"pensada para {opportunity.vertical}. ¿Te comparto cómo funcionaría?"
        )
        try:
            generated = await self._generate_outreach_body(opportunity, offer, channel)
            if generated:
                body = generated
        except Exception:
            pass  # Claude unavailable — use static fallback
        try:
            from brain.humanizer import humanize_text

            body = humanize_text(body, channel=channel)
        except Exception:
            pass  # humanizer is optional — never break outreach generation
        cta = "Responder para recibir propuesta"
        return OutreachMessage(channel=channel, subject=subject, body=body, cta=cta)

    async def detect_product_gaps(
        self,
        conversations: Iterable[dict[str, Any]],
        lost_deals: Iterable[dict[str, Any]],
    ) -> list[ProductIdea]:
        counter: Counter[str] = Counter()
        for row in conversations:
            for requested in row.get("requested_services") or []:
                counter[str(requested).strip().lower()] += 1
        for row in lost_deals:
            reason = str(row.get("lost_reason") or "")
            requested = str(row.get("requested_service") or "")
            if "no lo ofrecemos" in reason.lower() and requested:
                counter[requested.strip().lower()] += 2

        ideas: list[ProductIdea] = []
        for service, count in counter.most_common(5):
            if count < 2:
                continue
            estimated_monthly = count * 900
            ideas.append(
                ProductIdea(
                    product_name=service,
                    evidence_count=count,
                    estimated_monthly_revenue_mxn=estimated_monthly,
                    rationale=(
                        f"Detecté {count} menciones o pérdidas relacionadas con {service}. "
                        f"Podría abrir una línea de ingreso estimada en ${estimated_monthly:,} MXN/mes."
                    ),
                )
            )
        return ideas

    async def daily_hunt(
        self,
        *,
        google_maps_results: Iterable[dict[str, Any]] | None = None,
        instagram_results: Iterable[dict[str, Any]] | None = None,
        trends_results: Iterable[dict[str, Any]] | None = None,
        job_board_results: Iterable[OpportunityInput | dict[str, Any]] | None = None,
        competitor_activity_results: Iterable[OpportunityInput | dict[str, Any]] | None = None,
        complaints_results: Iterable[OpportunityInput | dict[str, Any]] | None = None,
        arbitrage_results: Iterable[OpportunityInput | dict[str, Any]] | None = None,
        internal_demand_results: Iterable[OpportunityInput | dict[str, Any]] | None = None,
        business_types: Iterable[str] = (),
        hashtags: Iterable[str] = (),
        trend_keywords: Iterable[str] = (),
        city: str = "Guadalajara",
        products: Iterable[str] = (),
        conversations: Iterable[dict[str, Any]] = (),
        lost_deals: Iterable[dict[str, Any]] = (),
    ) -> HuntReport:
        scanned: list[OpportunityInput] = []
        scanned.extend(await self.scan_google_maps(city, business_types, 5000, results=google_maps_results))
        scanned.extend(await self.scan_instagram(hashtags, city, results=instagram_results))
        scanned.extend(await self.scan_trends(trend_keywords, city, results=trends_results))
        for row in list(job_board_results or []):
            scanned.append(self._coerce_opportunity(row, source_hint="job_boards"))
        for row in list(competitor_activity_results or []):
            scanned.append(self._coerce_opportunity(row, source_hint="competitor_activity"))
        for row in list(complaints_results or []):
            scanned.append(self._coerce_opportunity(row, source_hint="complaints"))
        for row in list(arbitrage_results or []):
            scanned.append(self._coerce_opportunity(row, source_hint="arbitrage"))
        for row in list(internal_demand_results or []):
            scanned.append(self._coerce_opportunity(row, source_hint="internal_demand"))

        deduped_scanned: list[OpportunityInput] = []
        seen_keys: set[str] = set()
        for item in scanned:
            metadata = item.metadata or {}
            place_id = str(metadata.get("place_id") or "").strip().lower()
            business_name = item.business_name.strip().lower()
            dedupe_key = place_id or business_name
            if not dedupe_key or dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            deduped_scanned.append(item)

        scored = [await self.evaluate_opportunity(item) for item in deduped_scanned]
        scored.sort(key=lambda item: item.score, reverse=True)
        top: list[OpportunityScore] = []
        per_vertical: Counter[str] = Counter()
        for item in scored:
            if item.score <= 70:
                continue
            if per_vertical[item.vertical] >= 2:
                continue
            top.append(item)
            per_vertical[item.vertical] += 1
            if len(top) >= 5:
                break

        offers = [await self.generate_offer(item, products) for item in top[:3]]
        outreach = [
            await self.generate_outreach(item, offer, "whatsapp")
            for item, offer in zip(top[:3], offers, strict=False)
        ]
        product_gaps = await self.detect_product_gaps(conversations, lost_deals)

        return HuntReport(
            opportunities_scanned=len(deduped_scanned),
            top_opportunities=top,
            offers=offers,
            outreach_messages=outreach,
            product_gaps=product_gaps,
        )
