"""Multi-agent text humanizer for WhatsApp outbound messages.

Inspired by AI-Text-Humanizer-App's pipeline concept but reimplemented as an
LLM-based multi-agent chain tuned for Mexican Spanish commercial WhatsApp.

Architecture (``mode="multiagent"``):
    draft  →  Agent 1 (Rewriter)  →  Agent 2 (Trimmer)  →  Guard  →  final

The **Rewriter** converts robotic AI text into natural conversational tone.
The **Trimmer** shortens for mobile readability.
The **Guard** (regex-based, no LLM) verifies that numbers, prices, names,
links, and CTAs survive the rewrite — if anything is lost it reverts.

Configuration via env vars:
    HUMANIZER_ENABLED   true | false  (master switch, default false)
    HUMANIZER_MODE      off | simple | multiagent | auto  (default auto)
    HUMANIZER_MODEL     OpenAI model  (default gpt-4o-mini)
    HUMANIZER_MIN_LEN   skip texts shorter than this  (default 12)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional, Set

logger = logging.getLogger("kan_core.humanizer")

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_MIN_LEN = 12
_WHATSAPP_MAX_LEN = 1500

# ---------------------------------------------------------------------------
# Prompts — Mexican Spanish, commercial WhatsApp
# ---------------------------------------------------------------------------

_REWRITER_SYSTEM = (
    "Eres un experto en comunicación comercial por WhatsApp en México. "
    "Tu trabajo es tomar un mensaje generado por IA y reescribirlo para que "
    "suene como lo escribiría un asesor comercial humano real: natural, "
    "directo, amigable, profesional pero no formal. Responde siempre en "
    "primera persona cuando hables de lo que yo haré por el cliente.\n\n"
    "REGLAS ESTRICTAS:\n"
    "- Español mexicano conversacional (tuteo o usted según contexto, nada de "
    "  'vosotros')\n"
    "- Sonar como persona real, no como bot ni como soporte corporativo\n"
    "- Hablar con tono comercial consultivo: claro, cercano y orientado a avanzar\n"
    "- Si el mensaje original permite cerrar o avanzar, termina con una sola "
    "  pregunta útil o un siguiente paso concreto\n"
    "- NO cambies datos: números, precios, porcentajes, nombres, links, "
    "  horarios, fechas, direcciones\n"
    "- NO inventes información que no esté en el original\n"
    "- NO agregues emojis a menos que el original los tenga\n"
    "- NO uses frases robóticas: 'En relación a', 'Espero que esta "
    "  información sea de utilidad', 'Con gusto le informo', 'Quedo a sus "
    "  órdenes', 'Estoy aquí para ayudarte', 'Agradecemos tu mensaje'\n"
    "- NO uses apertura genérica tipo 'Hola, gracias por escribirnos' salvo "
    "  que el original ya lo requiera por contexto\n"
    "- Prioriza claridad y brevedad\n"
    "- Mantén el CTA (call-to-action) exactamente igual en intención\n"
    "- Si el original es corto y natural, cámbialo lo mínimo posible"
)

_TRIMMER_SYSTEM = (
    "Eres editor de mensajes de WhatsApp comerciales. Tu trabajo es acortar "
    "el mensaje sin perder información importante.\n\n"
    "REGLAS:\n"
    "- Máximo 3-4 oraciones para WhatsApp\n"
    "- Elimina relleno y repeticiones\n"
    "- Mantén tono humano y comercial, nunca robótico\n"
    "- Conserva: precios, nombres, links, CTAs, horarios, datos concretos\n"
    "- No agregues nada nuevo\n"
    "- Si el mensaje ya es corto, devuélvelo igual\n"
    "- Formato legible en pantalla de celular (líneas cortas)"
)

_SIMPLE_SYSTEM = (
    "Eres un experto en comunicación comercial por WhatsApp en México. "
    "Reescribe el siguiente mensaje para que suene natural, humano, directo "
    "y amigable — como un asesor real.\n\n"
    "REGLAS:\n"
    "- Español mexicano conversacional\n"
    "- Siempre en primera persona cuando hable de lo que yo haré por el cliente\n"
    "- Que suene a asesor comercial real, no a bot genérico\n"
    "- NO cambies datos: números, precios, nombres, links, horarios\n"
    "- NO inventes información\n"
    "- NO agregues emojis nuevos\n"
    "- Quita frases robóticas y aperturas corporativas\n"
    "- Máximo 3-4 oraciones, legible en celular\n"
    "- Mantén el CTA original\n"
    "- Si ya suena bien, cámbialo lo mínimo"
)


# ---------------------------------------------------------------------------
# Data-preservation guard (regex, no LLM)
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(
    r"(?:\$\s*[\d,]+(?:\.\d{1,2})?|[\d,]+(?:\.\d{1,2})?\s*(?:MXN|USD|pesos|dlls|dólares))",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d[\d,.]+\b")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{2,4}")


def _extract_anchors(text: str) -> Set[str]:
    """Extract data tokens that MUST survive humanization."""
    anchors: Set[str] = set()
    for pattern in (_PRICE_RE, _URL_RE, _EMAIL_RE):
        anchors.update(m.group() for m in pattern.finditer(text))
    for m in _NUMBER_RE.finditer(text):
        val = m.group().strip(" ,.")
        if len(val) >= 2:
            anchors.add(val)
    return anchors


def _guard_check(original: str, humanized: str) -> bool:
    """Return True if all critical data from *original* is present in
    *humanized*.  This is the non-LLM safety net."""
    anchors = _extract_anchors(original)
    if not anchors:
        return True
    missing = {a for a in anchors if a not in humanized}
    if missing:
        logger.warning(
            "[HUMANIZER] guard_failed missing=%s", missing
        )
        return False
    return True


# ---------------------------------------------------------------------------
# LLM calling (httpx — supports both Anthropic and OpenAI)
# ---------------------------------------------------------------------------

def _get_model() -> str:
    return str(os.getenv("HUMANIZER_MODEL") or _DEFAULT_MODEL).strip()


def _llm_call(system: str, user_text: str) -> str:
    """Single synchronous chat completion. Raises on failure.

    Routes to Anthropic API when the model starts with 'claude-',
    otherwise falls back to OpenAI-compatible endpoint.
    """
    import httpx

    model = _get_model()

    if model.startswith("claude"):
        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 800,
                "system": system,
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return str((data.get("content") or [{}])[0].get("text") or "").strip()

    else:
        from openai import OpenAI

        client = OpenAI()
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            temperature=0.5,
            max_tokens=800,
        )
        return (r.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _rewrite(text: str) -> str:
    return _llm_call(_REWRITER_SYSTEM, text)


def _trim(text: str) -> str:
    return _llm_call(_TRIMMER_SYSTEM, text)


def _simple_humanize(text: str) -> str:
    return _llm_call(_SIMPLE_SYSTEM, text)


def _run_multiagent(text: str) -> str:
    """Rewriter → Trimmer → Guard.  Raises on LLM failure."""
    step1 = _rewrite(text)
    logger.info("[HUMANIZER] agent_rewriter done len=%d→%d", len(text), len(step1))

    step2 = _trim(step1)
    logger.info("[HUMANIZER] agent_trimmer done len=%d→%d", len(step1), len(step2))

    if _guard_check(text, step2):
        logger.info("[HUMANIZER] guard_passed")
        return step2

    if _guard_check(text, step1):
        logger.warning("[HUMANIZER] guard_reverted_to_rewriter (trimmer lost data)")
        return step1

    logger.warning("[HUMANIZER] guard_reverted_to_original (both stages lost data)")
    return text


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _is_enabled() -> bool:
    return str(os.getenv("HUMANIZER_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes",
    }


def _get_mode() -> str:
    mode = str(os.getenv("HUMANIZER_MODE") or "auto").strip().lower()
    if mode in ("off", "simple", "multiagent", "auto"):
        return mode
    return "auto"


def _min_length() -> int:
    try:
        return int(os.getenv("HUMANIZER_MIN_LEN") or _DEFAULT_MIN_LEN)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_LEN


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def humanize_text(
    text: str,
    *,
    channel: str = "whatsapp",
    mode: Optional[str] = None,
) -> str:
    """Humanize *text* for outbound delivery.

    Parameters
    ----------
    text : str
        The draft response from the main agent.
    channel : str
        Target channel (``"whatsapp"`` applies length limits).
    mode : str | None
        Override for ``HUMANIZER_MODE``.  When ``None`` the env var is used.

    Returns
    -------
    str
        The humanized (or original) text.  **Never raises.**
    """
    original = str(text or "").strip()
    if not original:
        return original

    if not _is_enabled():
        return original

    effective_mode = mode or _get_mode()
    if effective_mode == "off":
        return original

    if len(original) < _min_length():
        logger.info(
            "[HUMANIZER] skipped (too short: %d < %d)",
            len(original), _min_length(),
        )
        return original

    logger.info(
        "[HUMANIZER] humanizer_started mode=%s channel=%s len=%d",
        effective_mode, channel, len(original),
    )

    result = original

    try:
        if effective_mode == "multiagent":
            result = _run_multiagent(original)

        elif effective_mode == "simple":
            result = _simple_humanize(original)

        elif effective_mode == "auto":
            try:
                result = _run_multiagent(original)
            except Exception:
                logger.warning(
                    "[HUMANIZER] humanizer_fallback_used multiagent→simple",
                    exc_info=True,
                )
                try:
                    result = _simple_humanize(original)
                except Exception:
                    logger.warning(
                        "[HUMANIZER] humanizer_fallback_used simple→original",
                        exc_info=True,
                    )
                    result = original

    except Exception:
        logger.exception("[HUMANIZER] humanizer_failed returning original")
        return original

    if not result or not result.strip():
        logger.warning("[HUMANIZER] humanizer_failed empty result, returning original")
        return original

    if channel == "whatsapp" and len(result) > _WHATSAPP_MAX_LEN:
        result = result[:_WHATSAPP_MAX_LEN].rsplit(" ", 1)[0] + "…"
        logger.info("[HUMANIZER] truncated for whatsapp len=%d", len(result))

    logger.info(
        "[HUMANIZER] humanizer_applied mode=%s len=%d→%d",
        effective_mode, len(original), len(result),
    )
    return result
