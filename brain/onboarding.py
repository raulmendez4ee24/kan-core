import unicodedata
from typing import Dict, List, Optional, Set

from schemas.onboarding import (
    OnboardingIntakeRequest,
    OnboardingIntakeResponse,
    RequiredIntegration,
)

_RULES = [
    {
        "integration": "meta",
        "priority": "high",
        "reason": "Canal conversacional en WhatsApp/Instagram/Messenger.",
        "keywords": {
            "whatsapp",
            "instagram",
            "messenger",
            "facebook",
            "meta",
        },
    },
    {
        "integration": "calendar",
        "priority": "high",
        "reason": "Agenda de citas, reservas o disponibilidad.",
        "keywords": {
            "cita",
            "citas",
            "agendar",
            "agenda",
            "appointment",
            "schedule",
            "reservar",
            "reserva",
            "calendario",
            "calendar",
        },
    },
    {
        "integration": "crm",
        "priority": "high",
        "reason": "Gestión de leads/clientes y seguimiento comercial.",
        "keywords": {
            "crm",
            "lead",
            "leads",
            "prospecto",
            "prospectos",
            "pipeline",
            "ventas",
            "venta",
            "cliente",
            "clientes",
            "cotizar",
            "cotizacion",
        },
    },
    {
        "integration": "payments",
        "priority": "medium",
        "reason": "Cobros, links de pago o validación de transacciones.",
        "keywords": {
            "pago",
            "pagos",
            "cobro",
            "cobros",
            "cobrar",
            "anticipo",
            "anticipos",
            "payment",
            "stripe",
            "paypal",
            "mercadopago",
            "mercado_pago",
        },
    },
    {
        "integration": "shopify",
        "priority": "medium",
        "reason": "Operación de ecommerce (ordenes, carrito, catalogo).",
        "keywords": {
            "shopify",
            "ecommerce",
            "e_commerce",
            "tienda",
            "pedido",
            "pedidos",
            "orden",
            "ordenes",
            "carrito",
            "checkout",
        },
    },
    {
        "integration": "inventory",
        "priority": "medium",
        "reason": "Control de stock, SKU y disponibilidad.",
        "keywords": {
            "inventario",
            "inventory",
            "stock",
            "sku",
            "almacen",
        },
    },
    {
        "integration": "helpdesk",
        "priority": "low",
        "reason": "Gestión de soporte, tickets y casos.",
        "keywords": {
            "soporte",
            "support",
            "ticket",
            "tickets",
            "helpdesk",
            "zendesk",
            "freshdesk",
        },
    },
]

_DOC_URL_RULES = [
    {
        "integration": "shopify",
        "priority": "medium",
        "reason": "Detectado por enlaces de documentacion de Shopify.",
        "keywords": {"shopify"},
    },
    {
        "integration": "payments",
        "priority": "medium",
        "reason": "Detectado por enlaces de API de pagos (Stripe/PayPal/Mercado Pago).",
        "keywords": {"stripe", "paypal", "mercadopago", "mercado-pago"},
    },
    {
        "integration": "crm",
        "priority": "high",
        "reason": "Detectado por enlaces de API CRM (HubSpot/Salesforce/Pipedrive).",
        "keywords": {"hubspot", "salesforce", "pipedrive", "zoho"},
    },
    {
        "integration": "calendar",
        "priority": "high",
        "reason": "Detectado por enlaces de API de agenda/calendario.",
        "keywords": {"calendly", "calendar", "google-calendar", "googlecalendar"},
    },
    {
        "integration": "helpdesk",
        "priority": "low",
        "reason": "Detectado por enlaces de API de soporte (Zendesk/Freshdesk).",
        "keywords": {"zendesk", "freshdesk", "helpscout", "intercom"},
    },
    {
        "integration": "meta",
        "priority": "high",
        "reason": "Detectado por enlaces de API de Meta/WhatsApp.",
        "keywords": {"graph.facebook.com", "meta", "whatsapp"},
    },
]

_PRIORITY_WEIGHT = {"high": 0, "medium": 1, "low": 2}
_WEBSITE_TEXT_METADATA_KEY = "__website_text"


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_text(value: str) -> str:
    return _strip_accents(value).lower().replace("-", " ").replace("_", " ")


def _collect_text_hints(data: OnboardingIntakeRequest) -> str:
    chunks: List[str] = [data.request_text]
    if data.business_type:
        chunks.append(data.business_type)
    if data.objective:
        chunks.append(data.objective)
    if data.channels:
        chunks.extend(data.channels)
    if data.desired_capabilities:
        chunks.extend(data.desired_capabilities)
    website_text = data.metadata.get(_WEBSITE_TEXT_METADATA_KEY)
    if isinstance(website_text, str) and website_text.strip():
        chunks.append(website_text)
    return _normalize_text(" ".join(chunks))


def _detect_required_integrations(data: OnboardingIntakeRequest) -> List[RequiredIntegration]:
    hints = _collect_text_hints(data)
    detected: Dict[str, RequiredIntegration] = {}

    for rule in _RULES:
        if any(keyword in hints for keyword in rule["keywords"]):
            detected[rule["integration"]] = RequiredIntegration(
                name=rule["integration"],
                reason=rule["reason"],
                priority=rule["priority"],
            )

    return sorted(
        detected.values(),
        key=lambda item: (_PRIORITY_WEIGHT[item.priority], item.name),
    )


def _detect_integrations_from_api_docs(urls: List[str]) -> List[RequiredIntegration]:
    detected: Dict[str, RequiredIntegration] = {}
    normalized_urls = [_normalize_text(url) for url in urls if url.strip()]
    for rule in _DOC_URL_RULES:
        if any(keyword in url for url in normalized_urls for keyword in rule["keywords"]):
            detected[rule["integration"]] = RequiredIntegration(
                name=rule["integration"],
                reason=rule["reason"],
                priority=rule["priority"],
            )
    return sorted(
        detected.values(),
        key=lambda item: (_PRIORITY_WEIGHT[item.priority], item.name),
    )


def _merge_required_integrations(
    keyword_required: List[RequiredIntegration],
    docs_required: List[RequiredIntegration],
) -> List[RequiredIntegration]:
    merged: Dict[str, RequiredIntegration] = {item.name: item for item in keyword_required}
    for item in docs_required:
        existing = merged.get(item.name)
        if not existing:
            merged[item.name] = item
            continue
        if _PRIORITY_WEIGHT[item.priority] < _PRIORITY_WEIGHT[existing.priority]:
            merged[item.name] = item
    return sorted(
        merged.values(),
        key=lambda item: (_PRIORITY_WEIGHT[item.priority], item.name),
    )


def _format_operator_message(
    data: OnboardingIntakeRequest,
    missing: List[str],
    pending: List[str],
    required: List[RequiredIntegration],
) -> str:
    company = data.company_name or "cliente_sin_nombre"
    objective = data.objective or "sin_objetivo_claro"
    appt = data.requested_appointment
    appt_text = "sin llamada solicitada"
    if appt and appt.requested_at:
        tz = appt.timezone or "UTC"
        appt_text = f"llamada solicitada para {appt.requested_at.isoformat()} ({tz})"

    reasons = {item.name: item.reason for item in required}
    missing_parts = [f"{name} ({reasons.get(name, 'acceso requerido')})" for name in missing]
    pending_parts = [f"{name} (pendiente por cliente)" for name in pending]

    return (
        f"Onboarding {company}: objetivo='{objective}'. "
        f"Faltantes: {', '.join(missing_parts) if missing_parts else 'ninguno'}. "
        f"Pendientes: {', '.join(pending_parts) if pending_parts else 'ninguno'}. "
        f"Estado llamada: {appt_text}."
    )


def analyze_onboarding_intake(
    data: OnboardingIntakeRequest,
    *,
    detected_api_docs: Optional[List[str]] = None,
    website_processed: bool = False,
    website_url: Optional[str] = None,
    ingestion_notes: Optional[List[str]] = None,
) -> OnboardingIntakeResponse:
    detected_api_docs = sorted(set(detected_api_docs or data.api_docs_urls))
    required_by_text = _detect_required_integrations(data)
    required_by_docs = _detect_integrations_from_api_docs(detected_api_docs)
    required = _merge_required_integrations(required_by_text, required_by_docs)
    required_names = {item.name for item in required}

    provided: Set[str] = {item.name for item in data.integrations if item.status == "has_access"}
    pending: Set[str] = {item.name for item in data.integrations if item.status == "pending"}

    missing = sorted(name for name in required_names if name not in provided)
    pending_required = sorted(name for name in required_names if name in pending)

    status = "ready_for_automation" if not missing else "needs_api_access"

    next_actions: List[str] = []
    if missing:
        for item in required:
            if item.name in missing:
                next_actions.append(f"Solicitar acceso API de {item.name}: {item.reason}")
    else:
        next_actions.append(
            "Registrar o actualizar integraciones y usar /integrations/auto-generate para crear workflows en n8n."
        )

    if data.requested_appointment and data.requested_appointment.requested_at:
        next_actions.append(
            "Confirmar la cita solicitada y enviar recordatorio al equipo operativo."
        )

    message = _format_operator_message(data, missing, pending_required, required)

    return OnboardingIntakeResponse(
        status=status,
        required_integrations=required,
        provided_integrations=sorted(provided),
        pending_integrations=pending_required,
        missing_integrations=missing,
        next_actions=next_actions,
        operator_message=message,
        n8n_dispatched=False,
        website_processed=website_processed,
        website_url=website_url,
        detected_api_docs=detected_api_docs,
        ingestion_notes=ingestion_notes or [],
    )
