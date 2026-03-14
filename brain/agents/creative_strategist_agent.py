from __future__ import annotations

from collections import defaultdict
from itertools import cycle
from string import Formatter
from typing import Any, Dict, Iterable, List

from brain.creative_memory import get_top_patterns
from brain.creative_patterns import CreativePatternTemplate, load_creative_patterns
from brain.skill_loader import load_skill

SKILL_NAME = "creative_hooks"
DOMAIN = "campaign_execution"
MIN_IDEA_COUNT = 15


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "resultado" if key == "outcome" else ""


def _route_for_temperature(temperature: str) -> str:
    if temperature == "hot":
        return "whatsapp_direct"
    if temperature == "warm":
        return "landing_to_whatsapp"
    return "instagram_profile"


def _format_template(template: str, values: Dict[str, str]) -> str:
    keys = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    payload = _SafeDict({key: values.get(key, "") for key in keys})
    return " ".join(template.format_map(payload).split()).strip()


def mutate_hook(hook: str, *, pattern_used: str, limit: int = 6) -> List[str]:
    clean = " ".join(str(hook or "").split()).strip()
    if not clean:
        return []
    limit = min(max(int(limit or 6), 5), 10)
    variations: List[str] = []
    lower_pattern = str(pattern_used or "").strip().lower()

    def _add(value: str) -> None:
        candidate = " ".join(str(value or "").split()).strip()
        if candidate and candidate != clean and candidate not in variations:
            variations.append(candidate)

    if lower_pattern in {"pain_loss", "hidden_cost", "silent_leak"}:
        _add(f"Lo caro no es el anuncio; es {clean.lower()}")
        _add(f"Cada semana que ignoras esto, {clean.lower()}")
        _add(f"El costo silencioso de tu embudo es simple: {clean.lower()}")
    elif lower_pattern in {"curiosity_gap", "myth_breaker", "contrarian"}:
        _add(f"La mayoria cree algo distinto, pero {clean[0].lower() + clean[1:]}")
        _add(f"Esto no pasa por falta de trafico: {clean[0].lower() + clean[1:]}")
        _add(f"Casi todos miran el lugar equivocado. {clean}")
    elif lower_pattern in {"proof", "one_metric", "authority"}:
        _add(f"Los datos ya lo gritan: {clean[0].lower() + clean[1:]}")
        _add(f"Despues de revisar cuentas reales, {clean[0].lower() + clean[1:]}")
        _add(f"Una metrica basta para verlo: {clean[0].lower() + clean[1:]}")
    elif lower_pattern in {"urgency", "future_cost", "decision_shortener"}:
        _add(f"Si hoy no lo corriges, {clean[0].lower() + clean[1:]}")
        _add(f"No necesitas esperar mas para esto: {clean}")
        _add(f"Tu siguiente venta puede depender de esto: {clean[0].lower() + clean[1:]}")
    else:
        _add(f"Hoy el verdadero cuello es este: {clean[0].lower() + clean[1:]}")
        _add(f"Lo que de verdad bloquea tus ventas: {clean[0].lower() + clean[1:]}")
        _add(f"Si quieres vender mejor, empieza aqui: {clean[0].lower() + clean[1:]}")

    _add(f"{clean} Hoy.")
    _add(f"{clean} Sin mas trafico.")
    _add(f"{clean} Antes de escalar presupuesto.")
    return variations[:limit]


def _context_values(offer_brief: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
    offer_label = str(offer_brief.get("recommended_offer") or "KAN Logic")
    primary_need = str(offer_brief.get("primary_need") or "general_automation")
    business_type = str(context.get("business_type") or context.get("vertical") or offer_brief.get("icp") or "tu negocio")
    outcome = {
        "meta_ads": "mas conversaciones calificadas y mas citas",
        "whatsapp": "mas respuestas y mas ventas",
        "ecommerce": "mas pedidos y menos friccion operativa",
        "autonomous_agent": "procesos mas rapidos y mejor trazabilidad",
        "general_automation": "mas seguimiento y mejor conversion",
    }.get(primary_need, "mas ventas")
    objection = ", ".join(list(offer_brief.get("objections") or [])[:2]).replace("_", " ") or "todavia no sabes si esto aplica a tu negocio"
    return {
        "offer": offer_label,
        "promise": str(offer_brief.get("promise") or ""),
        "problem": str(offer_brief.get("problem_statement") or ""),
        "cta": str(offer_brief.get("cta") or "pedirme una llamada corta"),
        "audience": business_type,
        "outcome": outcome,
        "common_belief": "que necesitas mas trafico",
        "better_path": "mejor seguimiento y mejor respuesta",
        "problem_unit": "lead",
        "hidden_cost": "ventas perdidas por respuesta lenta y seguimiento flojo",
        "surface_issue": "el anuncio",
        "hidden_cause": "lo que pasa despues del clic",
        "proof_statement": "La gente si pregunta; lo que falla es el seguimiento",
        "scene": "Te escriben, respondes tarde y luego se enfria la conversacion",
        "before_state": "te llegaban mensajes pero no habia orden en la respuesta",
        "after_state": "cada lead recibe seguimiento claro y rapido",
        "scope": "cuentas de servicio y embudos con WhatsApp",
        "objection": objection,
        "reframe": "el verdadero cuello suele estar en el seguimiento o la oferta",
        "mechanism": "la combinacion entre respuesta inmediata y mejor calificacion",
        "speed_claim": "mas rapido y con mejor contexto",
        "benefit_one": "responder en segundos",
        "benefit_two": "seguir leads sin perderlos",
        "benefit_three": "llevar prospectos a reunion",
        "task": "seguimiento comercial",
        "higher_value": "cerrar y operar mejor",
        "cta_step": "una llamada corta para revisar tu flujo actual",
        "metric_name": "tasa de respuesta",
        "metric_state": "caida",
        "input_flow": "trafico frio",
    }


def _matching_patterns(temperature: str) -> List[CreativePatternTemplate]:
    patterns = load_creative_patterns()
    preferred = [item for item in patterns if item.traffic_temperature in (temperature, "all")]
    return preferred or patterns


def _read_pattern(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _memory_ideas(memory_patterns: Iterable[Any], route: str, cta: str) -> List[Dict[str, Any]]:
    ideas: List[Dict[str, Any]] = []
    for index, item in enumerate(memory_patterns, start=1):
        pattern_used = str(_read_pattern(item, "pattern_used") or "memory")
        base_hook = str(_read_pattern(item, "hook") or "")
        base_angle = str(_read_pattern(item, "angle") or "")
        for variant_index, variant in enumerate(mutate_hook(base_hook, pattern_used=pattern_used, limit=5), start=1):
            ideas.append(
                {
                    "name": f"memory_{index}_{variant_index}",
                    "hook": variant,
                    "angle": base_angle or f"Reinterpretar un hook ya ganador para mantener el patron {pattern_used}.",
                    "cta": cta,
                    "route": route,
                    "pattern_used": pattern_used,
                    "category": "memory_variation",
                    "traffic_temperature": _read_pattern(item, "traffic_temperature"),
                    "inspiration_source": "creative_memory",
                }
            )
            if len(ideas) >= 5:
                return ideas
    return ideas


def _base_ideas(
    *,
    offer_brief: Dict[str, Any],
    context: Dict[str, Any],
    patterns: List[CreativePatternTemplate],
) -> List[Dict[str, Any]]:
    values = _context_values(offer_brief, context)
    cta = str(offer_brief.get("cta") or values["cta"])
    route = _route_for_temperature(str(offer_brief.get("buying_temperature") or "cold"))
    ideas: List[Dict[str, Any]] = []
    by_category: Dict[str, List[CreativePatternTemplate]] = defaultdict(list)
    for pattern in patterns:
        by_category[pattern.category].append(pattern)

    categories = list(by_category.keys())
    selection: List[CreativePatternTemplate] = []
    for pattern_list in by_category.values():
        if pattern_list:
            selection.append(pattern_list[0])
    category_cycle = cycle(categories)
    while len(selection) < MIN_IDEA_COUNT:
        category = next(category_cycle)
        bucket = by_category[category]
        pattern = bucket[len(selection) % len(bucket)]
        selection.append(pattern)

    for index, pattern in enumerate(selection[:MIN_IDEA_COUNT], start=1):
        hook = _format_template(pattern.template, values)
        angle = (
            f"Usar el patron {pattern.pattern_name} para conectar {values['problem']} con {values['outcome']}. "
            f"Riesgo principal: {pattern.risks[0]}"
        )
        ideas.append(
            {
                "name": f"{pattern.pattern_name}_{index}",
                "hook": hook,
                "angle": angle,
                "cta": cta,
                "route": route if str(offer_brief.get("buying_temperature") or "cold") != "hot" else _route_for_temperature("hot"),
                "pattern_used": pattern.pattern_name,
                "category": pattern.category,
                "traffic_temperature": str(offer_brief.get("buying_temperature") or "cold"),
                "example": pattern.example,
            }
        )
    return ideas


def build_creative_plan(
    *,
    offer_brief: Dict[str, Any],
    context: Dict[str, Any],
    campaign_id: str | None = None,
) -> Dict[str, Any]:
    loaded = load_skill(SKILL_NAME) or {"summary": ""}
    traffic_temperature = str(offer_brief.get("buying_temperature") or "cold")
    route = _route_for_temperature(traffic_temperature)
    top_patterns = get_top_patterns(domain=str(context.get("domain") or DOMAIN), limit=5)
    ideas = _memory_ideas(top_patterns, route, str(offer_brief.get("cta") or "")) + _base_ideas(
        offer_brief=offer_brief,
        context=context,
        patterns=_matching_patterns(traffic_temperature),
    )
    ideas = ideas[: max(MIN_IDEA_COUNT, len(ideas))]
    testing_plan = {
        "acquisition_pct": 60,
        "conversion_pct": 20,
        "commercial_pct": 15,
        "testing_pct": 5,
        "primary_objective": "engagement_or_profile_visits" if route == "instagram_profile" else "messages",
    }
    return {
        "skill": SKILL_NAME,
        "skill_summary": str(loaded.get("summary") or ""),
        "offer": str(offer_brief.get("recommended_offer") or "KAN Logic"),
        "campaign_id": campaign_id,
        "traffic_temperature": traffic_temperature,
        "ideas": ideas,
        "angles": ideas,
        "testing_plan": testing_plan,
        "inspiration": [
            {
                "hook": pattern.hook,
                "angle": pattern.angle,
                "pattern_used": pattern.pattern_used,
                "close_rate": pattern.close_rate,
                "booking_rate": pattern.booking_rate,
                "reply_rate": pattern.reply_rate,
            }
            for pattern in top_patterns
        ],
        "next_creative_batch": [item["hook"] for item in ideas[:5]],
    }
