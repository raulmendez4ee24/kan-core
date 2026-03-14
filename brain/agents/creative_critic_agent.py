from __future__ import annotations

from typing import Any, Dict, List


_GENERIC_TOKENS = ("crecer", "innovar", "potencial", "impulsar", "escala", "inteligencia artificial")
_OUTCOME_TOKENS = ("ventas", "citas", "seguimiento", "responder", "leads", "whatsapp", "reunion", "cliente")
_FEATURE_TOKENS = ("automatizacion", "integracion", "ia", "agente", "sistema", "dashboard", "bot")
_URGENT_TOKENS = ("hoy", "ya", "antes", "pierdes", "cuesta", "urgente", "sigue")


def _score_angle(angle: Dict[str, Any], *, offer_brief: Dict[str, Any]) -> Dict[str, Any]:
    hook = str(angle.get("hook") or "")
    description = str(angle.get("angle") or "")
    cta = str(angle.get("cta") or "")
    text = f"{hook} {description} {cta}".lower()
    temperature = str(angle.get("traffic_temperature") or offer_brief.get("buying_temperature") or "cold").lower()
    route = str(angle.get("route") or "")
    pattern_used = str(angle.get("pattern_used") or "unknown")

    clarity = 0.95 if 6 <= len(hook.split()) <= 17 else 0.7
    clarity -= 0.1 if any(token in hook.lower() for token in ("...", "??", "!!!")) else 0.0

    specificity = 0.95 if any(token in text for token in _OUTCOME_TOKENS) else 0.55
    if any(char.isdigit() for char in text):
        specificity += 0.05

    commercial_strength = 0.95 if any(token in text for token in ("ventas", "citas", "clientes", "roas", "reunion")) else 0.6
    scroll_stop_power = 0.9 if any(token in text for token in _URGENT_TOKENS) or "?" in hook else 0.65

    if temperature == "cold":
        funnel_fit = 0.9 if route == "instagram_profile" else 0.55
    elif temperature == "warm":
        funnel_fit = 0.9 if route in {"landing_to_whatsapp", "instagram_profile"} else 0.7
    else:
        funnel_fit = 0.9 if route == "whatsapp_direct" else 0.65

    differentiation = 0.9 if pattern_used not in {"unknown", "memory"} and any(token in text for token in ("seguimiento", "whatsapp", "embudo", "respuesta")) else 0.6

    flags: List[str] = []
    if specificity < 0.7 and any(token in text for token in _GENERIC_TOKENS):
        flags.append("generic")
    if scroll_stop_power < 0.72 and commercial_strength < 0.75:
        flags.append("too_safe")
    if not any(token in text for token in _OUTCOME_TOKENS):
        flags.append("too_abstract")
    if any(token in text for token in _FEATURE_TOKENS) and not any(token in text for token in _OUTCOME_TOKENS):
        flags.append("feature_not_outcome")
    if len(cta.split()) < 3 or not any(token in cta.lower() for token in ("demo", "llamada", "reunion", "diagnostico", "revisar", "agenda")):
        flags.append("weak_cta")

    notes: List[str] = []
    if "generic" in flags:
        notes.append("El hook suena amplio o intercambiable; necesita un dolor u outcome mas concreto.")
    if "too_safe" in flags:
        notes.append("Le falta tension comercial o un motivo claro para detener el scroll.")
    if "too_abstract" in flags:
        notes.append("Habla demasiado en abstracto y poco del problema real del cliente.")
    if "feature_not_outcome" in flags:
        notes.append("Describe funcionalidades antes que resultado de negocio.")
    if "weak_cta" in flags:
        notes.append("El CTA no empuja a una siguiente accion comercial clara.")

    penalty = 0.08 * len(flags)
    score = round(max(0.0, min(1.0, ((clarity + specificity + commercial_strength + scroll_stop_power + funnel_fit + differentiation) / 6.0) - penalty)), 4)
    approved = score >= 0.74 and "generic" not in flags and "too_abstract" not in flags and "weak_cta" not in flags

    return {
        "name": angle.get("name"),
        "pattern_used": pattern_used,
        "score": score,
        "clarity": round(clarity, 4),
        "specificity": round(specificity, 4),
        "commercial_strength": round(commercial_strength, 4),
        "scroll_stop_power": round(scroll_stop_power, 4),
        "funnel_fit": round(funnel_fit, 4),
        "differentiation": round(differentiation, 4),
        "flags": flags,
        "approved": approved,
        "notes": notes,
    }


def critique_creative_plan(*, offer_brief: Dict[str, Any], creative_plan: Dict[str, Any]) -> Dict[str, Any]:
    ideas = list(creative_plan.get("ideas") or creative_plan.get("angles") or [])
    reviews = [_score_angle(angle, offer_brief=offer_brief) for angle in ideas]
    reviews.sort(key=lambda item: (item["approved"], item["score"]), reverse=True)
    winner = next((item for item in reviews if item["approved"]), reviews[0] if reviews else {})
    weakest = reviews[-1] if reviews else {}
    rejected = [item for item in reviews if not item["approved"]]
    global_notes = list(dict.fromkeys(note for item in rejected[:3] for note in item["notes"]))[:3]
    return {
        "winner": winner,
        "weakest": weakest,
        "reviews": reviews,
        "approved": bool(winner and winner.get("approved")),
        "rejected_count": len(rejected),
        "global_notes": global_notes,
    }
