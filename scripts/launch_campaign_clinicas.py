#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.brand_director import BRAND_TZ, BrandDirector, ContentPost
from brain.forge import AdCreative, OfferForge, save_generated_offer
from brain.revenue_operators.hunter import HunterOperator, OpportunityInput


def _load_env() -> None:
    for name in (".env.secrets", ".env"):
        env_path = ROOT / name
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _planning_week_start(day: date) -> date:
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day - timedelta(days=day.weekday())


def _to_dental_opportunity(source: dict) -> OpportunityInput:
    metadata = dict(source.get("metadata") or {})
    metadata["original_vertical"] = source.get("vertical")
    metadata["campaign_vertical"] = "dental"
    return OpportunityInput(
        business_name=str(source.get("business_name") or "Unknown clinic"),
        vertical="dentists",
        city=str(source.get("city") or "Guadalajara"),
        has_website=bool(metadata.get("website")),
        has_bot=False,
        estimated_market_size=max(1, int(round(float(source.get("market_score") or 0.0) * 10))),
        pain_score=max(0.0, min(1.0, float(source.get("pain_score") or 0.0) / 100.0)),
        payment_capacity_score=max(0.0, min(1.0, float(source.get("payment_capacity_score") or 0.0) / 100.0)),
        competition_score=max(0.0, min(1.0, float(source.get("competition_score") or 0.0) / 100.0)),
        stack_fit_score=max(0.0, min(1.0, float(source.get("stack_fit_score") or 0.0) / 100.0)),
        source="clinics_campaign",
        metadata=metadata,
    )


def _forced_clinic_ad_creatives(offer, forced_hook: str) -> list[AdCreative]:
    proof_lines = [
        "Clínicas similares ya automatizan respuesta y agenda desde WhatsApp.",
        "Responder en minutos cambia cuántas citas sí terminan confirmadas.",
        "Cada consulta sin seguimiento termina siendo una cita menos en agenda.",
    ]
    visuals = [
        "Recepción de clínica dental premium en tonos azul/blanco; paciente escribiendo por WhatsApp mientras entra una cita confirmada.",
        "Dentista revisando agenda saturada y panel limpio mostrando respuestas automáticas y valoraciones agendadas.",
        "Comparativa visual: mensajes sin respuesta vs agenda llena con seguimiento automático por WhatsApp.",
    ]
    ctas = ["Quiero más citas", "Quiero verlo", "Quiero mi propuesta"]

    creatives: list[AdCreative] = []
    for index in range(3):
        body = (
            f"{forced_hook} "
            f"{proof_lines[index]} "
            f"{offer.offer.name} te ayuda a responder, filtrar y agendar sin depender de contestar manualmente. "
            f"Actívalo desde ${offer.offer.price_mxn:,.0f} MXN."
        )
        creatives.append(
            AdCreative(
                headline=forced_hook,
                body=body,
                cta_button_text=ctas[index],
                suggested_visual_description=visuals[index],
            )
        )
    return creatives


async def _save_dental_week(director: BrandDirector, reference_date: date) -> list[ContentPost]:
    await director.initialize()
    week_start = _planning_week_start(reference_date)
    base_plan = await director.generate_weekly_content_plan(reference_date=reference_date)
    topics = [
        "WhatsApp dental que agenda solo",
        "Recepción dental sin cuellos de botella",
        "Valoraciones que sí terminan en cita",
        "Seguimiento dental sin dejar leads fríos",
        "Menos no-shows, más pacientes",
        "Cierre dental más rápido por WhatsApp",
        "La agenda dental no debe frenarse",
    ]
    hooks = [
        "¿Cuántas citas perdiste hoy?",
        "Responder tarde vacía tu agenda.",
        "WhatsApp puede vender tus valoraciones.",
        "Cada lead frío cuesta pacientes.",
        "Tu recepción no debe saturarse.",
        "Agenda más sin contestar todo.",
        "Tus pacientes quieren respuesta ya.",
    ]
    rebuilt: list[ContentPost] = []
    for index, post in enumerate(base_plan):
        hook = hooks[index % len(hooks)]
        topic = topics[index % len(topics)]
        cta = "Escríbeme y te muestro cómo se vería en tu clínica."
        rebuilt.append(
            post.model_copy(
                update={
                    "pillar": "dental",
                    "vertical": "dental",
                    "topic": topic,
                    "hook": hook,
                    "full_script": (
                        f"{hook}\n\n"
                        "Contexto: clínicas y consultorios pierden pacientes cuando la respuesta por WhatsApp tarda.\n"
                        "Desarrollo: muestra cómo automatizar preguntas, filtro y agenda para cerrar más valoraciones.\n"
                        f"Cierre: {cta}"
                    ),
                    "visual_direction": "Dental premium en azul/blanco, limpio, confiable y orientado a citas.",
                    "caption": f"{hook} {cta}",
                    "cta": cta,
                }
            )
        )

    async with aiosqlite.connect(director.db_path) as conn:
        await conn.execute(
            """
            INSERT INTO weekly_content_plans (week_start, generated_at, plan_json)
            VALUES (?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                generated_at = excluded.generated_at,
                plan_json = excluded.plan_json
            """,
            (
                week_start.isoformat(),
                datetime.now(timezone.utc).isoformat(),
                json.dumps([item.model_dump(mode="json") for item in rebuilt], ensure_ascii=False),
            ),
        )
        await conn.commit()
    return rebuilt


async def main() -> None:
    _load_env()

    hunter = HunterOperator()
    hunt = await hunter.daily_hunt(
        business_types=["clinics"],
        city="Guadalajara",
        products=["paginas web personalizadas", "chatbots"],
    )
    prospects = [
        item
        for item in hunt.top_opportunities
        if str(item.vertical) == "clinics" and float(item.score) > 75.0
    ]

    forge = OfferForge()
    forced_hook = "¿Cuántas citas perdiste esta semana?"
    generated: list[dict] = []
    for item in prospects:
        item_payload = item.model_dump(mode="json")
        dental_opportunity = _to_dental_opportunity(item_payload)
        offer = await forge.forge_offer(dental_opportunity)
        landing_html = forge.generate_landing_page(offer)
        ad_creatives = _forced_clinic_ad_creatives(offer, forced_hook)
        record = save_generated_offer(offer, landing_html=landing_html, ad_creatives=ad_creatives)
        generated.append(
            {
                "business_name": item.business_name,
                "score": item.score,
                "offer_id": record.offer_id,
                "offer_name": record.offer.name,
                "price_mxn": record.offer.price_mxn,
                "landing_path": record.landing_path,
                "ad_creatives": [creative.model_dump(mode="json") for creative in ad_creatives],
            }
        )

    outbound_results = await hunter.send_clinic_outbound_campaign(prospects)

    director = BrandDirector()
    dental_week = await _save_dental_week(director, datetime.now(BRAND_TZ).date())

    print("=== CLINICAS CAMPAIGN REPORT ===")
    print(f"prospects_found={len(prospects)}")
    print(f"offers_generated={len(generated)}")
    print(f"brand_director_dental_days={len(dental_week)}")
    print("")

    print("=== PROSPECTS ===")
    for item in prospects:
        print(
            f"- {item.business_name} | city={item.city} | score={item.score} | "
            f"pain={item.pain_score} | payment={item.payment_capacity_score}"
        )
    if not prospects:
        print("- none")
    print("")

    print("=== OFFERS / LANDINGS / ADS ===")
    for item in generated:
        print(
            f"- {item['business_name']} | offer={item['offer_name']} | price_mxn={item['price_mxn']} | "
            f"landing_path={item['landing_path']}"
        )
        for idx, creative in enumerate(item["ad_creatives"], start=1):
            print(
                f"  creative_{idx}: headline={creative['headline']} | "
                f"cta={creative['cta_button_text']}"
            )
    if not generated:
        print("- none")
    print("")

    print("=== WHATSAPP OUTBOUND ===")
    for result in outbound_results:
        print(
            f"- {result.business_name} | phone={result.phone_number or 'none'} | "
            f"status={result.status} | reason={result.reason or 'ok'}"
        )
    if not outbound_results:
        print("- none")
    print("")

    print("=== BRAND DIRECTOR NEXT 7 DAYS ===")
    for post in dental_week:
        print(
            f"- {post.post_id} | day={post.day_index} | pillar={post.pillar} | "
            f"vertical={post.vertical} | topic={post.topic} | hook={post.hook}"
        )


if __name__ == "__main__":
    asyncio.run(main())
