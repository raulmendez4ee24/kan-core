import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


def _normalize_token(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = lowered.replace("-", "_").replace(" ", "_")
    lowered = re.sub(r"[^a-z0-9_]", "", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered


_FUNCTION_SYNONYMS: Dict[str, Set[str]] = {
    "crm": {
        "crm",
        "lead",
        "leads",
        "sales",
        "ventas",
        "pipeline",
        "prospectos",
    },
    "calendar": {
        "calendar",
        "calendario",
        "schedule",
        "scheduling",
        "appointment",
        "appointments",
        "citas",
        "agendar",
    },
    "payments": {
        "payments",
        "payment",
        "pagos",
        "pago",
        "cobros",
        "cobro",
        "checkout",
        "billing",
    },
    "helpdesk": {
        "helpdesk",
        "support",
        "soporte",
        "ticket",
        "tickets",
    },
    "shopify": {
        "shopify",
        "ecommerce",
        "tienda",
        "orders",
        "ordenes",
        "pedido",
    },
    "inventory": {
        "inventory",
        "inventario",
        "stock",
        "sku",
    },
    "meta": {
        "meta",
        "whatsapp",
        "instagram",
        "messenger",
        "facebook",
    },
    "email": {
        "email",
        "correo",
        "mail",
        "newsletter",
        "smtp",
    },
    "messaging": {
        "messaging",
        "sms",
        "twilio",
        "whatsapp",
        "notification",
        "notificaciones",
    },
}


@dataclass(frozen=True)
class ProviderOption:
    provider_id: str
    provider_name: str
    integration_name: str
    base_url: str
    docs_url: str
    openapi_url: Optional[str]
    auth_type: str
    header_name: Optional[str] = None
    registration_mode: str = "manual"  # manual|api
    env_key_name: Optional[str] = None
    signup_url: Optional[str] = None
    registration_url: Optional[str] = None
    registration_method: str = "POST"
    registration_key_field: str = "api_key"
    healthcheck_path: Optional[str] = None
    keyword_signals: Set[str] = field(default_factory=set)
    quality_score: float = 0.7
    setup_score: float = 0.5
    coverage_score: float = 0.7


_PROVIDER_CATALOG: List[ProviderOption] = [
    ProviderOption(
        provider_id="hubspot",
        provider_name="HubSpot",
        integration_name="crm",
        base_url="https://api.hubapi.com",
        docs_url="https://developers.hubspot.com/docs/api/overview",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="HUBSPOT_API_KEY",
        signup_url="https://app.hubspot.com/signup-hubspot/crm",
        healthcheck_path="/crm/v3/objects/contacts?limit=1",
        keyword_signals={"b2b", "sales", "marketing", "inbound"},
        quality_score=0.86,
        setup_score=0.72,
        coverage_score=0.9,
    ),
    ProviderOption(
        provider_id="pipedrive",
        provider_name="Pipedrive",
        integration_name="crm",
        base_url="https://api.pipedrive.com",
        docs_url="https://developers.pipedrive.com/docs/api/v1",
        openapi_url=None,
        auth_type="apiKey",
        header_name="x-api-token",
        env_key_name="PIPEDRIVE_API_KEY",
        signup_url="https://www.pipedrive.com/en/register",
        healthcheck_path="/v1/deals?limit=1",
        keyword_signals={"pipeline", "ventas", "deals"},
        quality_score=0.78,
        setup_score=0.81,
        coverage_score=0.82,
    ),
    ProviderOption(
        provider_id="calendly",
        provider_name="Calendly",
        integration_name="calendar",
        base_url="https://api.calendly.com",
        docs_url="https://developer.calendly.com/getting-started",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="CALENDLY_API_KEY",
        signup_url="https://calendly.com/signup",
        healthcheck_path="/users/me",
        keyword_signals={"appointment", "meeting", "bookings", "citas"},
        quality_score=0.84,
        setup_score=0.8,
        coverage_score=0.81,
    ),
    ProviderOption(
        provider_id="google_calendar",
        provider_name="Google Calendar",
        integration_name="calendar",
        base_url="https://www.googleapis.com/calendar/v3",
        docs_url="https://developers.google.com/workspace/calendar/api/guides/overview",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="GOOGLE_CALENDAR_API_KEY",
        signup_url="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
        healthcheck_path="/users/me/calendarList",
        keyword_signals={"google", "workspace", "calendar"},
        quality_score=0.82,
        setup_score=0.58,
        coverage_score=0.92,
    ),
    ProviderOption(
        provider_id="stripe",
        provider_name="Stripe",
        integration_name="payments",
        base_url="https://api.stripe.com",
        docs_url="https://docs.stripe.com/api",
        openapi_url="https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
        auth_type="bearer",
        env_key_name="STRIPE_API_KEY",
        signup_url="https://dashboard.stripe.com/register",
        healthcheck_path="/v1/customers?limit=1",
        keyword_signals={"checkout", "suscripciones", "cards", "payments"},
        quality_score=0.9,
        setup_score=0.8,
        coverage_score=0.92,
    ),
    ProviderOption(
        provider_id="paypal",
        provider_name="PayPal",
        integration_name="payments",
        base_url="https://api-m.paypal.com",
        docs_url="https://developer.paypal.com/api/rest/",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="PAYPAL_API_KEY",
        signup_url="https://www.paypal.com/bizsignup",
        healthcheck_path="/v1/identity/oauth2/userinfo?schema=paypalv1.1",
        keyword_signals={"paypal", "wallet", "payments"},
        quality_score=0.8,
        setup_score=0.68,
        coverage_score=0.78,
    ),
    ProviderOption(
        provider_id="mercado_pago",
        provider_name="Mercado Pago",
        integration_name="payments",
        base_url="https://api.mercadopago.com",
        docs_url="https://www.mercadopago.com/developers/es/docs",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="MERCADO_PAGO_API_KEY",
        signup_url="https://www.mercadopago.com.mx/developers",
        healthcheck_path="/v1/payments/search?limit=1",
        keyword_signals={"latam", "mercado", "pagos"},
        quality_score=0.78,
        setup_score=0.74,
        coverage_score=0.8,
    ),
    ProviderOption(
        provider_id="zendesk",
        provider_name="Zendesk",
        integration_name="helpdesk",
        base_url="https://api.zendesk.com",
        docs_url="https://developer.zendesk.com/api-reference/introduction/",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="ZENDESK_API_KEY",
        signup_url="https://www.zendesk.com/register/",
        healthcheck_path="/api/v2/tickets.json?page[size]=1",
        keyword_signals={"tickets", "support", "soporte"},
        quality_score=0.82,
        setup_score=0.67,
        coverage_score=0.84,
    ),
    ProviderOption(
        provider_id="freshdesk",
        provider_name="Freshdesk",
        integration_name="helpdesk",
        base_url="https://api.freshdesk.com",
        docs_url="https://developers.freshdesk.com/api/",
        openapi_url=None,
        auth_type="apiKey",
        header_name="Authorization",
        env_key_name="FRESHDESK_API_KEY",
        signup_url="https://freshdesk.com/signup",
        healthcheck_path="/api/v2/tickets?per_page=1",
        keyword_signals={"tickets", "support", "helpdesk"},
        quality_score=0.76,
        setup_score=0.78,
        coverage_score=0.78,
    ),
    ProviderOption(
        provider_id="sendgrid",
        provider_name="SendGrid",
        integration_name="email",
        base_url="https://api.sendgrid.com",
        docs_url="https://www.twilio.com/docs/sendgrid/api-reference",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="SENDGRID_API_KEY",
        signup_url="https://signup.sendgrid.com/",
        healthcheck_path="/v3/verified_senders",
        keyword_signals={"email", "smtp", "newsletter"},
        quality_score=0.84,
        setup_score=0.78,
        coverage_score=0.88,
    ),
    ProviderOption(
        provider_id="resend",
        provider_name="Resend",
        integration_name="email",
        base_url="https://api.resend.com",
        docs_url="https://resend.com/docs/api-reference",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="RESEND_API_KEY",
        signup_url="https://resend.com/signup",
        healthcheck_path="/domains",
        keyword_signals={"email", "transactional", "mail"},
        quality_score=0.82,
        setup_score=0.86,
        coverage_score=0.8,
    ),
    ProviderOption(
        provider_id="twilio",
        provider_name="Twilio",
        integration_name="messaging",
        base_url="https://api.twilio.com",
        docs_url="https://www.twilio.com/docs/usage/api",
        openapi_url=None,
        auth_type="basic",
        env_key_name="TWILIO_API_KEY",
        signup_url="https://www.twilio.com/try-twilio",
        healthcheck_path="/2010-04-01/Accounts.json?PageSize=1",
        keyword_signals={"sms", "phone", "otp", "messaging"},
        quality_score=0.86,
        setup_score=0.66,
        coverage_score=0.9,
    ),
    ProviderOption(
        provider_id="whatsapp_cloud",
        provider_name="WhatsApp Cloud API",
        integration_name="messaging",
        base_url="https://graph.facebook.com/v20.0",
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="WHATSAPP_CLOUD_API_KEY",
        signup_url="https://developers.facebook.com/apps/",
        healthcheck_path="/me",
        keyword_signals={"whatsapp", "cloud", "meta"},
        quality_score=0.83,
        setup_score=0.62,
        coverage_score=0.86,
    ),
    ProviderOption(
        provider_id="shopify_admin",
        provider_name="Shopify Admin API",
        integration_name="shopify",
        base_url="https://{shop}.myshopify.com/admin/api/2024-10",
        docs_url="https://shopify.dev/docs/api/admin-rest",
        openapi_url=None,
        auth_type="apiKey",
        header_name="X-Shopify-Access-Token",
        env_key_name="SHOPIFY_API_KEY",
        signup_url="https://www.shopify.com",
        healthcheck_path="/products.json?limit=1",
        keyword_signals={"orders", "catalog", "ecommerce", "tienda"},
        quality_score=0.86,
        setup_score=0.59,
        coverage_score=0.9,
    ),
    ProviderOption(
        provider_id="meta_graph",
        provider_name="Meta Graph API",
        integration_name="meta",
        base_url="https://graph.facebook.com/v20.0",
        docs_url="https://developers.facebook.com/docs/graph-api",
        openapi_url=None,
        auth_type="bearer",
        env_key_name="META_API_KEY",
        signup_url="https://developers.facebook.com/apps/",
        healthcheck_path="/me",
        keyword_signals={"whatsapp", "instagram", "messenger"},
        quality_score=0.83,
        setup_score=0.52,
        coverage_score=0.9,
    ),
]


def normalize_function_name(value: str) -> Optional[str]:
    token = _normalize_token(value)
    if not token:
        return None
    if token in _FUNCTION_SYNONYMS:
        return token

    for canonical, synonyms in _FUNCTION_SYNONYMS.items():
        if token in synonyms:
            return canonical
        if any(word in token for word in synonyms):
            return canonical
    return token


def infer_required_functions(request_text: str, desired_functions: List[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()

    for item in desired_functions:
        normalized = normalize_function_name(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    normalized_text = _normalize_token(request_text).replace("_", " ")
    for canonical, synonyms in _FUNCTION_SYNONYMS.items():
        if canonical in seen:
            continue
        check_terms = set(synonyms)
        check_terms.add(canonical)
        if any(term.replace("_", " ") in normalized_text for term in check_terms):
            seen.add(canonical)
            result.append(canonical)

    return result


def _score_provider(option: ProviderOption, normalized_text: str) -> tuple[float, List[str]]:
    score = (
        option.quality_score * 45.0
        + option.setup_score * 25.0
        + option.coverage_score * 20.0
        + (5.0 if option.openapi_url else 0.0)
    )

    hits = 0
    for token in option.keyword_signals:
        if token.replace("_", " ") in normalized_text:
            hits += 1
    score += min(float(hits) * 4.0, 12.0)

    notes = [
        f"quality={option.quality_score:.2f}",
        f"setup={option.setup_score:.2f}",
        f"coverage={option.coverage_score:.2f}",
    ]
    if option.openapi_url:
        notes.append("openapi_available")
    if hits:
        notes.append(f"keyword_hits={hits}")
    if option.registration_mode == "api":
        notes.append("supports_api_registration")
    return round(min(score, 100.0), 2), notes


def rank_provider_candidates(
    *,
    function_name: str,
    request_text: str,
    allowlist: List[str],
    blocklist: List[str],
    limit: int,
) -> List[Dict[str, object]]:
    normalized_allow = {_normalize_token(item) for item in allowlist if _normalize_token(item)}
    normalized_block = {_normalize_token(item) for item in blocklist if _normalize_token(item)}
    normalized_text = _normalize_token(request_text).replace("_", " ")

    options: List[Dict[str, object]] = []
    for option in _PROVIDER_CATALOG:
        if option.integration_name != function_name:
            continue
        provider_id = _normalize_token(option.provider_id)
        if normalized_allow and provider_id not in normalized_allow:
            continue
        if provider_id in normalized_block:
            continue
        score, notes = _score_provider(option, normalized_text)
        options.append(
            {
                "provider_id": option.provider_id,
                "provider_name": option.provider_name,
                "integration_name": option.integration_name,
                "base_url": option.base_url,
                "docs_url": option.docs_url,
                "openapi_url": option.openapi_url,
                "auth_type": option.auth_type,
                "header_name": option.header_name,
                "registration_mode": option.registration_mode,
                "signup_url": option.signup_url or option.docs_url,
                "registration_url": option.registration_url,
                "registration_method": option.registration_method,
                "registration_key_field": option.registration_key_field,
                "env_key_name": option.env_key_name,
                "healthcheck_path": option.healthcheck_path,
                "score": score,
                "comparison_notes": notes,
            }
        )

    options.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item["provider_name"]).lower(),
        )
    )
    return options[: max(1, limit)]
