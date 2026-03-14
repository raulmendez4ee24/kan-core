from __future__ import annotations

from typing import Any, Dict, List

from brain.skill_loader import load_skill

SKILL_NAME = "offer_analysis"

_OFFERS = [
    {
        "name": "starter",
        "label": "Starter",
        "fit": {"whatsapp", "lead_followup", "agenda", "faq"},
        "promise": "responder y captar leads por WhatsApp 24/7",
        "cta": "agendar una llamada corta para revisar tu flujo actual",
    },
    {
        "name": "growth",
        "label": "Growth",
        "fit": {"whatsapp", "meta_ads", "seguimiento", "crm", "cobranza"},
        "promise": "convertir mas conversaciones en seguimiento y ventas",
        "cta": "revisar embudo, respuesta y seguimiento en una reunion",
    },
    {
        "name": "commerce",
        "label": "Commerce",
        "fit": {"ecommerce", "shopify", "pedidos", "postventa"},
        "promise": "automatizar atencion, pedidos y recuperacion de carritos",
        "cta": "revisar catalogo, pedidos y postventa en una demo",
    },
    {
        "name": "enterprise",
        "label": "Enterprise",
        "fit": {"autonomous_agent", "multi_process", "operaciones", "pc", "browser"},
        "promise": "automatizar procesos criticos con agentes autonomos y trazabilidad",
        "cta": "hacer un diagnostico mas profundo de operaciones y ROI",
    },
]


def _text_blob(goal: str, context: Dict[str, Any]) -> str:
    values = [goal] + [str(value) for value in context.values()]
    return " ".join(values).strip().lower()


def _classify_need(text: str) -> str:
    if any(token in text for token in ("shopify", "ecommerce", "pedido", "carrito", "tienda")):
        return "ecommerce"
    if any(token in text for token in ("autonom", "pc", "navegador", "multi proceso", "multi-proceso")):
        return "autonomous_agent"
    if any(token in text for token in ("meta ads", "facebook ads", "instagram ads", "campa", "lead", "ventas", "crm")):
        return "meta_ads"
    if any(token in text for token in ("whatsapp", "faq", "cita", "agenda", "responder")):
        return "whatsapp"
    return "general_automation"


def _detect_objections(text: str) -> List[str]:
    objections: List[str] = []
    if any(token in text for token in ("caro", "precio", "costoso", "presupuesto")):
        objections.append("sensibilidad_a_precio")
    if any(token in text for token in ("no se", "como funciona", "explicame", "duda")):
        objections.append("falta_claridad")
    if any(token in text for token in ("urgente", "ya", "hoy", "cuanto tardan")):
        objections.append("urgencia_alta")
    return objections


def _recommended_offer(primary_need: str) -> Dict[str, Any]:
    token_map = {
        "whatsapp": {"whatsapp", "lead_followup", "agenda", "faq"},
        "meta_ads": {"meta_ads", "seguimiento", "crm"},
        "ecommerce": {"ecommerce", "shopify", "pedidos", "postventa"},
        "autonomous_agent": {"autonomous_agent", "multi_process", "operaciones"},
        "general_automation": {"lead_followup", "seguimiento", "crm"},
    }
    target = token_map.get(primary_need, {"lead_followup"})
    for offer in _OFFERS:
        if offer["fit"] & target:
            return offer
    return _OFFERS[1]


def analyze_offer(*, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
    text = _text_blob(goal, context)
    if any(key in context for key in ("daily_spend_usd", "ctr_drop_pct", "roas", "budget_usd", "audience_size", "campaign_name")):
        primary_need = "meta_ads"
    else:
        primary_need = _classify_need(text)
    offer = _recommended_offer(primary_need)
    objections = _detect_objections(text)
    loaded = load_skill(SKILL_NAME) or {"summary": ""}

    if primary_need in {"meta_ads", "whatsapp"}:
        icp = "negocio de servicios o negocio local que depende de WhatsApp y seguimiento"
    elif primary_need == "ecommerce":
        icp = "negocio que vende catalogo o tienda y necesita automatizar pedidos y postventa"
    else:
        icp = "empresa que necesita automatizar procesos y responder mas rapido"

    problem = {
        "whatsapp": "pierde conversaciones por respuesta lenta y falta de seguimiento",
        "meta_ads": "genera trafico o mensajes pero no convierte suficiente en citas o ventas",
        "ecommerce": "atiende pedidos y postventa con demasiado trabajo manual",
        "autonomous_agent": "opera procesos criticos de forma manual y lenta",
        "general_automation": "pierde eficiencia operativa y seguimiento comercial",
    }.get(primary_need, "pierde eficiencia y seguimiento comercial")

    buying_temperature = "warm" if any(token in text for token in ("precio", "cotiza", "demo", "agendar", "implement")) else "cold"
    if any(token in text for token in ("quiero comprar", "contratar", "empezar hoy")):
        buying_temperature = "hot"

    return {
        "skill": SKILL_NAME,
        "skill_summary": str(loaded.get("summary") or ""),
        "primary_need": primary_need,
        "icp": icp,
        "problem_statement": problem,
        "recommended_offer": offer["label"],
        "offer_code": offer["name"],
        "promise": offer["promise"],
        "cta": offer["cta"],
        "objections": objections,
        "buying_temperature": buying_temperature,
        "products_of_interest": [primary_need],
    }
