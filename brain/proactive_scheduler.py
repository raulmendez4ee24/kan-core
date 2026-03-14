"""Proactive Outreach Scheduler.

Allows kan-core to initiate conversations without waiting for user input.
The LLM generates the outreach message based on a trigger context (event,
schedule, alert) and dispatches it through any configured channel.

Architecture:
    ProactiveOutreach (DB row / payload)
        ├── trigger_type: "scheduled" | "event" | "alert"
        ├── channel: "telegram" | "slack" | "discord" | "teams"
        ├── target_id: channel/chat/user ID on that platform
        ├── client_id: organization UUID
        └── context: arbitrary dict (order data, alert payload, etc.)

    ProactiveScheduler.run_outreach(outreach) → dispatches message

Env vars:
    ENABLE_PROACTIVE_OUTREACH=true   — master switch
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from brain.core import CognitiveEngine
from observability import capture_exception

logger = logging.getLogger("kan_core.proactive_scheduler")

_PROACTIVE_SYSTEM = """\
Eres un agente de outreach proactivo de una empresa. Tu tarea es redactar un mensaje \
breve, profesional y relevante para enviar al usuario o canal indicado, basándote \
en el contexto del trigger.

REGLAS:
1. El mensaje debe ser conciso (máximo 3 líneas).
2. Usa el idioma apropiado para el canal y contexto (español por defecto).
3. No inventes datos fuera del contexto proporcionado.
4. El tono debe ser el de una empresa seria, no spam.
5. Devuelve SOLO el texto del mensaje, sin prefijos ni explicaciones.

CONTEXTO DEL TRIGGER:
{context}
"""


@dataclass
class ProactiveOutreach:
    """Represents a proactive outreach job."""

    client_id: str
    channel: str  # telegram | slack | discord | teams
    target_id: str  # chat_id, channel_id, webhook_url, etc.
    trigger_type: str = "scheduled"  # scheduled | event | alert
    context: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    custom_message: Optional[str] = None  # skip LLM if provided


class ProactiveScheduler:
    """Executes proactive outreach by generating and dispatching messages."""

    async def run_outreach(self, outreach: ProactiveOutreach) -> bool:
        """Generate and dispatch a proactive message.

        Returns True if dispatched successfully, False otherwise.
        """
        if os.getenv("ENABLE_PROACTIVE_OUTREACH", "false").lower() not in {"1", "true", "yes"}:
            logger.debug("Proactive outreach disabled (ENABLE_PROACTIVE_OUTREACH)")
            return False

        message = outreach.custom_message
        if not message:
            message = await self._generate_message(outreach)
        if not message:
            return False

        return await self._dispatch(outreach, message)

    async def _generate_message(self, outreach: ProactiveOutreach) -> Optional[str]:
        """Ask the LLM to craft the outreach message from context."""
        try:
            import json as _json

            context_str = _json.dumps(outreach.context, ensure_ascii=False, indent=2)
            system_prompt = _PROACTIVE_SYSTEM.format(context=context_str)

            engine = CognitiveEngine.get_instance()
            model_name = engine._resolve_model_name(None)
            body = engine._build_request(
                system_prompt=system_prompt,
                history=[],
                message="Redacta el mensaje de outreach ahora.",
                model_name=model_name,
                temperature=0.6,
            )
            response_json = await engine._call_model(model_name, body)
            text = engine._extract_text(response_json).strip()
            return text or None
        except Exception as exc:
            capture_exception(exc, {"source": "proactive_generate", "client_id": outreach.client_id})
            logger.exception("Failed to generate proactive message")
            return None

    async def _dispatch(self, outreach: ProactiveOutreach, message: str) -> bool:
        """Send the message through the appropriate channel."""
        channel = outreach.channel.lower()
        target = outreach.target_id

        try:
            if channel == "telegram":
                from tools.telegram_client import send_telegram_message
                return await send_telegram_message(target, message)

            if channel == "slack":
                from tools.slack_client import send_slack_message
                return await send_slack_message(target, message)

            if channel == "discord":
                from tools.discord_client import send_discord_message
                return await send_discord_message(target, message)

            if channel == "teams":
                from tools.teams_client import send_teams_message
                return await send_teams_message(target, message)

            logger.warning("Unknown proactive channel: %s", channel)
            return False
        except Exception as exc:
            capture_exception(
                exc,
                {
                    "source": "proactive_dispatch",
                    "client_id": outreach.client_id,
                    "channel": channel,
                },
            )
            logger.exception("Proactive dispatch failed")
            return False
