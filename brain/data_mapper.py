import re
from typing import Any, Dict, Iterable, Tuple

_ALIASES = {
    "email": {
        "email",
        "correo",
        "correo_electronico",
        "client_email",
        "user_mail",
        "mail",
        "e_mail",
    },
    "phone": {
        "phone",
        "telefono",
        "telefono_movil",
        "mobile",
        "cellphone",
        "whatsapp_number",
    },
    "name": {
        "name",
        "nombre",
        "full_name",
        "client_name",
        "user_name",
    },
    "company": {
        "company",
        "empresa",
        "business_name",
        "organization",
        "org",
    },
    "industry": {
        "industry",
        "industria",
        "sector",
        "vertical",
    },
    "website": {
        "website",
        "sitio_web",
        "web",
        "url",
    },
}


def _normalize_key(value: str) -> str:
    token = (value or "").strip().lower()
    token = token.replace("-", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_]", "", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def _canonical_key(raw_key: str) -> str:
    normalized = _normalize_key(raw_key)
    if not normalized:
        return raw_key
    for canonical, aliases in _ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def auto_map_fields(
    payload: Dict[str, Any],
    *,
    allowed_fields: Iterable[str] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    mapped: Dict[str, Any] = {}
    trace: Dict[str, str] = {}
    allowed = set(allowed_fields or [])

    for original_key, value in payload.items():
        canonical = _canonical_key(str(original_key))
        if canonical != str(original_key):
            trace[str(original_key)] = canonical
        if allowed and canonical not in allowed and original_key in allowed:
            canonical = str(original_key)
        if canonical in mapped and mapped[canonical] not in (None, "", []):
            continue
        mapped[canonical] = value
    return mapped, trace


def normalize_industry(value: Any) -> str:
    if not isinstance(value, str):
        return "general"
    token = _normalize_key(value)
    return token or "general"
