from typing import Any, Dict, List


_INDUSTRY_PLAYBOOKS: Dict[str, List[Dict[str, Any]]] = {
    "clinics": [
        {"step_type": "integration", "title": "Activar chatbot de WhatsApp", "desired_functions": ["meta"]},
        {"step_type": "automation", "title": "Configurar recordatorios de cita", "desired_functions": ["calendar", "messaging"]},
        {"step_type": "crm", "title": "Crear pipeline de pacientes", "desired_functions": ["crm"]},
    ],
    "restaurants": [
        {"step_type": "integration", "title": "Activar pedidos por chat", "desired_functions": ["messaging", "payments"]},
        {"step_type": "automation", "title": "Recordatorios de reservas", "desired_functions": ["calendar", "messaging"]},
    ],
    "real_estate": [
        {"step_type": "integration", "title": "Captura de leads inmobiliarios", "desired_functions": ["crm"]},
        {"step_type": "automation", "title": "Seguimiento de visitas", "desired_functions": ["calendar", "email"]},
    ],
    "gyms": [
        {"step_type": "integration", "title": "Registro de prospectos", "desired_functions": ["crm"]},
        {"step_type": "automation", "title": "Renovaciones y cobranza", "desired_functions": ["payments", "messaging"]},
    ],
    "ecommerce": [
        {"step_type": "integration", "title": "Sincronizar tienda y CRM", "desired_functions": ["shopify", "crm"]},
        {"step_type": "automation", "title": "Recuperacion de carrito", "desired_functions": ["email", "messaging"]},
    ],
}


def normalize_industry(value: str | None) -> str:
    token = (value or "").strip().lower().replace(" ", "_")
    return token or "general"


def build_mission_steps(
    *,
    goal: str,
    recommendations: List[Dict[str, Any]],
    industry: str | None,
) -> List[Dict[str, Any]]:
    normalized_industry = normalize_industry(industry)
    steps: List[Dict[str, Any]] = []

    playbook_steps = _INDUSTRY_PLAYBOOKS.get(normalized_industry, [])
    for item in playbook_steps:
        steps.append(
            {
                "step_type": item["step_type"],
                "title": item["title"],
                "desired_functions": list(item.get("desired_functions", [])),
                "request_text": f"{goal}. {item['title']}",
            }
        )

    for rec in recommendations:
        desired_functions = [str(x) for x in rec.get("desired_functions", []) if str(x).strip()]
        steps.append(
            {
                "step_type": "recommendation",
                "title": str(rec.get("proposal") or "Aplicar recomendacion"),
                "desired_functions": desired_functions,
                "request_text": str(rec.get("request_text") or goal),
            }
        )

    if not steps:
        steps = [
            {
                "step_type": "baseline",
                "title": "Activar CRM y seguimiento automatico",
                "desired_functions": ["crm", "messaging"],
                "request_text": f"{goal}. Activar CRM y seguimiento automatico.",
            }
        ]

    unique_steps: List[Dict[str, Any]] = []
    seen = set()
    for step in steps:
        key = (step["title"].strip().lower(), tuple(sorted(step["desired_functions"])))
        if key in seen:
            continue
        seen.add(key)
        unique_steps.append(step)

    for idx, step in enumerate(unique_steps, start=1):
        step["step_order"] = idx

    return unique_steps
