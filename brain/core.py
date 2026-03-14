from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.env_loader import is_placeholder as _env_is_placeholder
from config.env_loader import load_environment
from brain.commercial_agenda import maybe_handle_special_request
from brain.memory import NoOpMemory, VectorMemory
from brain.local_memory import LocalPersistentMemory
from brain.skill_loader import format_skills_for_prompt, load_relevant_skills
from database import AsyncSessionLocal
from models import ApiIntegration, Client, ConversationLog, TokenUsage
from observability import capture_exception
from tools.retry import request_with_retry

logger = logging.getLogger("kan_core.cognitive")

load_environment(context="brain.core")

_OPENAI_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{19,}$")


def _is_uuid_text(raw: str) -> bool:
    token = str(raw or "").strip()
    if not token:
        return False
    try:
        uuid.UUID(token)
        return True
    except Exception:
        return False


def _is_openai_key_like(raw: str) -> bool:
    token = str(raw or "").strip()
    if not token:
        return False
    if token.startswith("sk-"):
        return len(token) >= 24
    return bool(_OPENAI_KEY_RE.match(token))


def validate_runtime_environment(
    *,
    context: str = "runtime",
    require_client_id: bool = False,
    require_openai: bool = False,
    strict_client_id_format: bool = False,
    fail_fast: bool = True,
) -> None:
    errors: List[str] = []
    jarvis_client_id = str(os.getenv("JARVIS_CLIENT_ID") or "").strip()
    if require_client_id:
        if _env_is_placeholder(jarvis_client_id):
            errors.append("JARVIS_CLIENT_ID (missing)")
        elif strict_client_id_format and not _is_uuid_text(jarvis_client_id):
            errors.append("JARVIS_CLIENT_ID (invalid UUID)")
    elif jarvis_client_id and strict_client_id_format and not _is_uuid_text(jarvis_client_id):
        errors.append("JARVIS_CLIENT_ID (invalid UUID)")

    openai_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if require_openai:
        if _env_is_placeholder(openai_key):
            errors.append("OPENAI_API_KEY (missing)")
        elif not _is_openai_key_like(openai_key):
            errors.append("OPENAI_API_KEY (invalid format)")
    elif openai_key and not _is_openai_key_like(openai_key):
        errors.append("OPENAI_API_KEY (invalid format)")

    if errors:
        message = f"[ENV] Invalid required vars for {context}: {', '.join(errors)}"
        if fail_fast:
            raise RuntimeError(message)
        logger.warning(message)


validate_runtime_environment(
    context="brain.core",
    require_client_id=False,
    # Keep import-time non-fatal by default; startup channels enforce strict mode.
    require_openai=False,
    strict_client_id_format=False,
    fail_fast=False,
)

# ---------------------------------------------------------------------------
# Sandbox eligibility heuristics (module-level for easy patching in tests)
# ---------------------------------------------------------------------------
_SANDBOX_CONNECTORS = frozenset({
    "y luego", "y después", "entonces también", "finalmente",
    "después de eso", "luego de", "posteriormente",
    "and then", "then send", "then notify", "after that", "finally",
    "first fetch", "first check", "first get",
})
_SANDBOX_ACTION_VERBS = frozenset({
    "revisa", "review", "check", "busca", "fetch", "obtén", "get",
    "manda", "send", "envía", "notifica", "notify",
    "crea", "create", "actualiza", "update",
    "genera", "generate", "exporta", "export",
    "sincroniza", "sync", "agrega", "add",
})
# Keywords that always trigger the agent even without multiple verbs
_SANDBOX_FORCE_KEYWORDS = frozenset({
    # n8n / workflows
    "flujo", "workflow", "automatiza", "automatización", "automation",
    "n8n", "integra", "integration", "pipeline",
    # API / discovery
    "api", "endpoint", "conéctate", "conecta", "connect",
    # Data operations
    "reporté", "reporte", "report", "dashboard", "analiza", "analytic",
    "inventario", "inventory", "stock", "pedido", "order", "factura", "invoice",
    # CRM / contacts
    "crm", "contacto", "contact", "lead", "cliente", "customer",
    # Outreach
    "slack", "discord", "telegram", "whatsapp", "correo", "email",
})

try:
    from brain.supabase_memory import SupabaseMemory
except Exception:
    class SupabaseMemory:  # type: ignore[no-redef]
        @classmethod
        def get_instance(cls) -> VectorMemory:
            raise RuntimeError("Supabase memory is unavailable")

DEFAULT_SYSTEM_PROMPT = (
    "Eres K'an, asistente de alta gama con capacidad de ejecucion real. "
    "Operas con acceso a desktop_agent, browser_agent e integraciones conectadas. "
    "No niegues control del entorno: en este runtime si puedes ejecutar tareas en la PC. "
    "Tu tono es profesional, eficiente y ligeramente ingenioso; eres un colaborador proactivo, no sumiso. "
    "Usa espanol de Mexico neutro y evita traducciones literales o tono robotico. "
    "Cuando hables con prospectos o clientes, responde como un asesor humano real: en primera persona, "
    "natural, breve y orientado a mover la conversacion al siguiente paso util. "
    "Evita respuestas genéricas, demasiado institucionales o que suenen a bot. "
    "Si el usuario da una orden de ejecucion, responde con confirmacion breve ('Entendido', 'En marcha' o 'Procediendo') "
    "y avanza sin pedir confirmacion adicional. "
    "Si necesitas informacion para completar una tarea, busca con las herramientas disponibles — nunca asumas ni inventes. "
    "Si una accion externa es necesaria, responde SOLO con un JSON valido que incluya: "
    "contract_version (usa \"1.0\"), action, arguments, rationale, e integration (nombre de integracion o \"none\"). "
    "Si no, responde en texto plano con resultados directos."
)
IDENTITY_STYLE_PROMPT = (
    "Capa de identidad obligatoria: "
    "1) Nunca digas que no puedes controlar la PC en este entorno. "
    "2) Prioriza ejecucion con pasos concretos y verificables. "
    "3) Evita lenguaje de duda innecesaria cuando hay orden explicita de ejecutar. "
    "4) Mantente conciso, claro y orientado a resultado."
)
BIAS_GUARD_PROMPT = (
    "Bias guard: avoid assumptions about demographics, protected classes, or user intent. "
    "Prefer factual, neutral, and verifiable guidance."
)
AUTOMATION_GUIDE_PROMPT = (
    "Automation guide: if you propose actions, keep them auditable, reversible when possible, "
    "and explicit about risk and expected outcome."
)


class CognitiveEngine:
    _instance: Optional["CognitiveEngine"] = None

    def __init__(self, api_key: str) -> None:
        self._provider = self._detect_provider()
        self._api_key = api_key
        if self._provider == "openai":
            self._base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        elif self._provider == "anthropic":
            self._base_url = os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1")
        elif self._provider == "ollama":
            self._base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        else:
            self._base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
        self._timeout = float(os.getenv("GEMINI_TIMEOUT", os.getenv("OPENAI_TIMEOUT", "20")))
        self._retries = int(os.getenv("GEMINI_RETRIES", os.getenv("OPENAI_RETRIES", "3")))
        self._backoff_base = float(os.getenv("GEMINI_BACKOFF_BASE", os.getenv("OPENAI_BACKOFF_BASE", "0.5")))
        self._backoff_max = float(os.getenv("GEMINI_BACKOFF_MAX", os.getenv("OPENAI_BACKOFF_MAX", "8.0")))
        self._max_output_tokens = os.getenv("GEMINI_MAX_OUTPUT_TOKENS") or os.getenv("OPENAI_MAX_OUTPUT_TOKENS")
        self._enable_bias_guard = os.getenv("ENABLE_BIAS_GUARD", "true").lower() in {"1", "true", "yes"}
        self._max_context_chars = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "1200"))
        self._memory = self._build_memory()

    @classmethod
    def _detect_provider(cls) -> str:
        explicit = (os.getenv("LLM_PROVIDER") or os.getenv("AI_PROVIDER") or "").strip().lower()
        if explicit in {"openai", "gemini", "anthropic", "ollama", "local"}:
            return "ollama" if explicit == "local" else explicit
        # OLLAMA_BASE_URL set → local inference without any cloud key
        if os.getenv("OLLAMA_BASE_URL"):
            return "ollama"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        return "openai"

    @classmethod
    def get_instance(cls) -> "CognitiveEngine":
        if cls._instance is None:
            provider = cls._detect_provider()
            if provider == "openai":
                api_key = os.getenv("OPENAI_API_KEY", "")
            elif provider == "anthropic":
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
            elif provider == "ollama":
                api_key = "ollama"  # local inference needs no real key
            else:
                api_key = os.getenv("GEMINI_API_KEY", "")
            cls._instance = cls(api_key)
        return cls._instance

    def _build_memory(self) -> VectorMemory:
        try:
            return SupabaseMemory.get_instance()
        except Exception as exc:
            # In tests/local dev we allow NoOp fallback to keep suite deterministic.
            allow_noop = (
                os.getenv("ALLOW_NOOP_MEMORY", "false").lower() in {"1", "true", "yes"}
                or "PYTEST_CURRENT_TEST" in os.environ
            )
            if allow_noop:
                return NoOpMemory()
            # Production-safe fallback: local persistent memory (non-NoOp).
            try:
                return LocalPersistentMemory.get_instance()
            except Exception as local_exc:
                raise RuntimeError(
                    "Memory backend is required in production (Supabase unavailable and local memory failed). "
                    "Set SUPABASE_URL/SUPABASE_KEY or fix LOCAL_MEMORY_STORE path."
                ) from local_exc

    async def generate_response(self, client_id: str, session_id: str, message: str) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            return await self._generate_with_session(
                session,
                client_id=client_id,
                session_id=session_id,
                message=message,
            )

    async def _generate_with_session(
        self,
        session: AsyncSession,
        *,
        client_id: str,
        session_id: str,
        message: str,
    ) -> Dict[str, Any]:
        client_uuid = self._parse_client_id(client_id)
        client = await session.get(Client, client_uuid)
        if not client:
            raise ValueError("Client not found")
        # Cache client fields early; session rollbacks can expire ORM state.
        client_system_prompt = client.system_prompt
        client_model_name = client.model_name
        client_temperature = client.temperature

        special_response = await maybe_handle_special_request(
            session,
            organization_id=client_uuid,
            session_id=session_id,
            message=message,
        )
        if special_response is not None:
            assistant_text = str(special_response.get("content") or "")
            await self._persist_conversation(session, client_uuid, session_id, message, assistant_text)
            try:
                await self._memory.store_interaction(
                    client_id=str(client_uuid),
                    session_id=session_id,
                    content=message,
                    metadata={"role": "user"},
                )
                await self._memory.store_interaction(
                    client_id=str(client_uuid),
                    session_id=session_id,
                    content=assistant_text,
                    metadata={"role": "assistant", "source": "commercial_agenda"},
                )
            except Exception:
                pass
            return special_response

        context_chunks: List[str] = []
        try:
            found = await self._memory.search_context(
                message,
                top_k=5,
                filters={"client_id": str(client_uuid), "session_id": session_id},
            )
            context_chunks = [str(x) for x in (found or [])]
        except Exception:
            context_chunks = []

        integrations = await self._load_active_integrations(session, client_uuid)
        active_integration_names = [
            str(getattr(item, "name", "")).strip().lower()
            for item in integrations
            if str(getattr(item, "name", "")).strip()
        ]
        relevant_skills = load_relevant_skills(message, active_integration_names)
        skills_prompt = format_skills_for_prompt(relevant_skills)

        # --- LLM-in-Sandbox ReAct loop (optional, backwards-compatible) ---
        if os.getenv("ENABLE_LLM_SANDBOX", "false").lower() in {"1", "true", "yes"}:
            if self._is_sandbox_eligible(message):
                try:
                    from brain.llm_sandbox import LLMSandbox

                    sandbox = LLMSandbox(engine=self)
                    sb_result = await sandbox.run(
                        goal=message,
                        client_id=client_id,
                        session_id=session_id,
                        integrations=integrations,
                    )
                    return self._sandbox_result_to_response(sb_result)
                except Exception as exc:
                    logger.exception(
                        "LLMSandbox failed, falling back to single-shot: %s", exc
                    )
        # --- End sandbox routing — fall through to single-shot ---

        history = await self._load_history(session, client_uuid, session_id)

        system_prompt = self._compose_system_prompt(
            client_prompt=client_system_prompt,
            context=context_chunks,
            integrations=integrations,
            skills_prompt=skills_prompt,
        )
        model_name = self._resolve_model_name(client_model_name)
        temperature = client_temperature if client_temperature is not None else 0.4
        request_body = self._build_request(
            system_prompt=system_prompt,
            history=history,
            message=message,
            model_name=model_name,
            temperature=temperature,
        )

        try:
            response_json = await self._call_model(model_name, request_body)
        except Exception as exc:
            logger.exception("Model request failed")
            capture_exception(exc, {"source": self._provider, "client_id": client_id})
            raise

        assistant_text = self._extract_text(response_json)
        usage = self._extract_usage(response_json)

        await self._persist_conversation(session, client_uuid, session_id, message, assistant_text)
        if usage:
            await self._persist_usage(session, client_uuid, usage)

        try:
            await self._memory.store_interaction(
                client_id=str(client_uuid),
                session_id=session_id,
                content=message,
                metadata={"role": "user"},
            )
            await self._memory.store_interaction(
                client_id=str(client_uuid),
                session_id=session_id,
                content=assistant_text,
                metadata={"role": "assistant"},
            )
        except Exception:
            pass

        action = self._extract_action(assistant_text)
        if action:
            return {"type": "action", "payload": action, "content": assistant_text}
        return {"type": "message", "content": assistant_text}

    def _parse_client_id(self, client_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(client_id)
        except ValueError as exc:
            raise ValueError("client_id must be a UUID string") from exc

    async def _load_active_integrations(
        self, session: AsyncSession, client_id: uuid.UUID
    ) -> List[ApiIntegration]:
        stmt = (
            select(ApiIntegration)
            .where(ApiIntegration.client_id == client_id, ApiIntegration.is_active.is_(True))
            .order_by(ApiIntegration.updated_at.desc())
            .limit(20)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_history(
        self, session: AsyncSession, client_id: uuid.UUID, session_id: str
    ) -> List[ConversationLog]:
        stmt = (
            select(ConversationLog)
            .where(
                ConversationLog.client_id == client_id,
                ConversationLog.session_id == session_id,
            )
            .order_by(ConversationLog.timestamp.desc())
            .limit(10)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    def _compose_system_prompt(
        self,
        *,
        client_prompt: Optional[str],
        context: Optional[List[str]],
        integrations: Optional[List[Any]] = None,
        skills_prompt: Optional[str] = None,
    ) -> str:
        base = str(client_prompt or DEFAULT_SYSTEM_PROMPT).strip()
        parts = [base, IDENTITY_STYLE_PROMPT, AUTOMATION_GUIDE_PROMPT]
        if self._enable_bias_guard:
            parts.append(BIAS_GUARD_PROMPT)

        if context:
            merged = "\n".join(str(x) for x in context if str(x).strip())[: self._max_context_chars]
            if merged:
                parts.append("Retrieved context (untrusted; validate before relying on it):\n" + merged)

        if integrations:
            lines: List[str] = []
            for integ in integrations:
                try:
                    row = {
                        "integration": str(getattr(integ, "name", "")),
                        "base_url": getattr(integ, "base_url", None),
                        "auth_type": getattr(integ, "auth_type", None),
                        "metadata": getattr(integ, "integration_metadata", None),
                    }
                    lines.append(json.dumps(row, ensure_ascii=True))
                except Exception:
                    continue
            if lines:
                parts.append("Available integrations (JSON lines):\n" + "\n".join(lines))

        if skills_prompt:
            parts.append("Relevant operating skills:\n" + str(skills_prompt))

        return "\n\n".join(x for x in parts if x)

    def _resolve_model_name(self, requested: Optional[str]) -> str:
        return self._resolve_model_name_for_provider(self._provider, requested)

    def _resolve_model_name_for_provider(self, provider: str, requested: Optional[str]) -> str:
        req = str(requested or "").strip()
        p = str(provider or "").strip().lower()
        if p == "openai":
            if not req or req.startswith("gemini") or req.startswith("claude"):
                return os.getenv("OPENAI_MODEL", "gpt-5.2-chat-latest")
            return req
        if p == "anthropic":
            if not req or req.startswith("gpt-") or req.startswith("gemini"):
                return os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            return req
        if p == "ollama":
            foreign = req.startswith(("gpt-", "gemini", "claude"))
            if not req or foreign:
                return os.getenv("OLLAMA_MODEL", "llama3.2")
            return req
        if not req or req.startswith("gpt-") or req.startswith("claude"):
            return os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        return req

    def _provider_order(self) -> List[str]:
        primary = str(self._provider or "openai").strip().lower() or "openai"
        order: List[str] = [primary]
        enabled = os.getenv("ENABLE_MODEL_FAILOVER", "true").lower() in {"1", "true", "yes"}
        if not enabled:
            return order
        chain_raw = str(
            os.getenv(
                "LLM_FAILOVER_CHAIN",
                "openai,anthropic,gemini,ollama",
            )
        )
        for token in chain_raw.split(","):
            item = str(token or "").strip().lower()
            if item in {"openai", "anthropic", "gemini", "ollama"} and item not in order:
                order.append(item)
        return order

    def _provider_credentials(self, provider: str) -> Tuple[str, str]:
        p = str(provider or "").strip().lower()
        if p == "openai":
            return (
                os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
                str(os.getenv("OPENAI_API_KEY", "")).strip(),
            )
        if p == "anthropic":
            return (
                os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1"),
                str(os.getenv("ANTHROPIC_API_KEY", "")).strip(),
            )
        if p == "ollama":
            return (
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                "ollama",
            )
        return (
            os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"),
            str(os.getenv("GEMINI_API_KEY", "")).strip(),
        )

    def _normalize_prompt_and_messages(self, body: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]], float, Optional[int]]:
        system_prompt = ""
        messages: List[Dict[str, str]] = []
        temperature = float(
            body.get("temperature")
            or (body.get("generationConfig") or {}).get("temperature")
            or 0.7
        )
        max_tokens: Optional[int] = None
        raw_max = body.get("max_tokens")
        if raw_max is None:
            raw_max = (body.get("generationConfig") or {}).get("maxOutputTokens")
        if raw_max is not None:
            try:
                max_tokens = int(raw_max)
            except Exception:
                max_tokens = None

        if isinstance(body.get("messages"), list):
            for item in list(body.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                content = str(item.get("content") or "")
                if role == "system":
                    system_prompt = content
                    continue
                if role in {"user", "assistant"}:
                    messages.append({"role": role, "content": content})
            return system_prompt, messages, temperature, max_tokens

        if isinstance(body.get("system"), str):
            system_prompt = str(body.get("system") or "")
        if isinstance(body.get("messages"), list):
            for item in list(body.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip().lower()
                if role in {"user", "assistant"}:
                    messages.append({"role": role, "content": str(item.get("content") or "")})
            return system_prompt, messages, temperature, max_tokens

        system_inst = (body.get("systemInstruction") or {}).get("parts") or []
        if system_inst and isinstance(system_inst, list):
            first = dict(system_inst[0] or {})
            system_prompt = str(first.get("text") or "")
        contents = list(body.get("contents") or [])
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = ""
            parts = list(item.get("parts") or [])
            if parts:
                text = str((dict(parts[0] or {})).get("text") or "")
            if role in {"user", "model"}:
                messages.append({"role": "assistant" if role == "model" else "user", "content": text})
        return system_prompt, messages, temperature, max_tokens

    def _build_provider_body(
        self,
        *,
        provider: str,
        model_name: str,
        system_prompt: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        p = str(provider or "").strip().lower()
        if p in {"openai", "ollama"}:
            payload: Dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "system", "content": system_prompt}, *messages],
                "temperature": float(temperature),
            }
            if str(model_name).startswith("gpt-5"):
                payload["temperature"] = 1.0
            if max_tokens is not None:
                payload["max_tokens"] = int(max_tokens)
            return payload
        if p == "anthropic":
            payload = {
                "model": model_name,
                "system": system_prompt,
                "messages": messages,
                "temperature": float(temperature),
                "max_tokens": int(max_tokens or os.getenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "1024")),
            }
            return payload
        generation_config: Dict[str, Any] = {"temperature": float(temperature)}
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = int(max_tokens)
        contents: List[Dict[str, Any]] = []
        for item in messages:
            role = "model" if str(item.get("role")) == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(item.get("content") or "")}]})
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

    def _build_request(
        self,
        *,
        system_prompt: str,
        history: List[Any],
        message: str,
        model_name: str,
        temperature: float,
    ) -> Dict[str, Any]:
        if self._provider in {"openai", "ollama"}:
            messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            for item in history:
                role = "user" if getattr(item, "role", "") == "user" else "assistant"
                messages.append({"role": role, "content": str(getattr(item, "content", ""))})
            messages.append({"role": "user", "content": message})
            # Some GPT-5 chat endpoints currently only accept default temperature.
            resolved_temp = float(temperature)
            if str(model_name).startswith("gpt-5"):
                resolved_temp = 1.0
            body: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": resolved_temp,
            }
            if self._max_output_tokens:
                body["max_tokens"] = int(self._max_output_tokens)
            return body
        if self._provider == "anthropic":
            messages: List[Dict[str, Any]] = []
            for item in history:
                role = "user" if getattr(item, "role", "") == "user" else "assistant"
                messages.append({"role": role, "content": str(getattr(item, "content", ""))})
            messages.append({"role": "user", "content": message})
            body = {
                "model": model_name,
                "system": system_prompt,
                "messages": messages,
                "temperature": temperature,
            }
            if self._max_output_tokens:
                body["max_tokens"] = int(self._max_output_tokens)
            else:
                body["max_tokens"] = int(os.getenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "1024"))
            return body

        contents: List[Dict[str, Any]] = []
        for item in history:
            role = "user" if getattr(item, "role", "") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": str(getattr(item, "content", ""))}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        generation_config: Dict[str, Any] = {"temperature": temperature}
        if self._max_output_tokens:
            generation_config["maxOutputTokens"] = int(self._max_output_tokens)

        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

    async def _call_model(self, model_name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        system_prompt, messages, temperature, max_tokens = self._normalize_prompt_and_messages(body)
        order = self._provider_order()
        errors: List[Dict[str, str]] = []
        last_exc: Optional[Exception] = None
        for provider in order:
            base_url, api_key = self._provider_credentials(provider)
            if provider != "ollama" and not api_key:
                errors.append({"provider": provider, "error": "missing_api_key"})
                continue
            resolved_model = self._resolve_model_name_for_provider(provider, model_name)
            provider_body = self._build_provider_body(
                provider=provider,
                model_name=resolved_model,
                system_prompt=system_prompt,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            try:
                if provider in {"openai", "ollama"}:
                    payload = await self._call_openai(provider_body, base_url=base_url, api_key=api_key)
                elif provider == "anthropic":
                    payload = await self._call_anthropic(provider_body, base_url=base_url, api_key=api_key)
                else:
                    payload = await self._call_gemini(resolved_model, provider_body, base_url=base_url, api_key=api_key)
                payload["_provider"] = provider
                if errors:
                    payload["_failover_trace"] = errors
                return payload
            except Exception as exc:
                last_exc = exc
                errors.append({"provider": provider, "error": str(type(exc).__name__)})
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError("No model provider available")

    async def _call_gemini(
        self,
        model_name: str,
        body: Dict[str, Any],
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{(base_url or self._base_url).rstrip('/')}/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": (api_key if api_key is not None else self._api_key)}
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                "POST",
                url,
                json=body,
                params=params,
                headers=headers,
                timeout=self._timeout,
                retries=self._retries,
                backoff_base=self._backoff_base,
                backoff_max=self._backoff_max,
            )
            response.raise_for_status()
            return response.json()

    async def _call_openai(
        self,
        body: Dict[str, Any],
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = api_key if api_key is not None else self._api_key
        url = f"{(base_url or self._base_url).rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=self._timeout,
                retries=self._retries,
                backoff_base=self._backoff_base,
                backoff_max=self._backoff_max,
            )
            response.raise_for_status()
            return response.json()

    async def _call_anthropic(
        self,
        body: Dict[str, Any],
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = api_key if api_key is not None else self._api_key
        url = f"{(base_url or self._base_url).rstrip('/')}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        }
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=self._timeout,
                retries=self._retries,
                backoff_base=self._backoff_base,
                backoff_max=self._backoff_max,
            )
            response.raise_for_status()
            return response.json()

    async def stream_response(
        self,
        *,
        client_id: str,
        session_id: str,
        message: str,
    ):
        """Async generator that yields text chunks as SSE data lines.

        Yields strings formatted as SSE events:
          data: {"token": "<chunk>"}\n\n

        Final event:
          data: [DONE]\n\n

        Falls back to simulated word-by-word streaming for providers that do
        not natively support streaming (Gemini) or if the stream fails.
        """
        import json as _json
        import asyncio as _asyncio

        model_name = self._resolve_model_name(None)
        system_prompt = self._compose_system_prompt(client_prompt=None, context=None)
        body = self._build_request(
            system_prompt=system_prompt,
            history=[],
            message=message,
            model_name=model_name,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )

        def _sse(token: str) -> str:
            return f"data: {_json.dumps({'token': token}, ensure_ascii=False)}\n\n"

        # OpenAI / Ollama — native streaming
        if self._provider in {"openai", "ollama"}:
            body["stream"] = True
            url = f"{self._base_url.rstrip('/')}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "text/event-stream",
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout * 3) as client:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for raw_line in resp.aiter_lines():
                            raw_line = raw_line.strip()
                            if not raw_line or raw_line == ":":
                                continue
                            if raw_line.startswith("data: "):
                                raw_line = raw_line[6:]
                            if raw_line == "[DONE]":
                                break
                            try:
                                chunk = _json.loads(raw_line)
                                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                token = str(delta.get("content") or "")
                                if token:
                                    yield _sse(token)
                            except Exception:
                                continue
                yield "data: [DONE]\n\n"
                return
            except Exception as exc:
                logger.warning("OpenAI stream failed, falling back: %s", exc)

        # Anthropic — native streaming
        if self._provider == "anthropic":
            body["stream"] = True
            url = f"{self._base_url.rstrip('/')}/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
                "Accept": "text/event-stream",
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout * 3) as client:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for raw_line in resp.aiter_lines():
                            raw_line = raw_line.strip()
                            if not raw_line or raw_line.startswith("event:"):
                                continue
                            if raw_line.startswith("data: "):
                                raw_line = raw_line[6:]
                            try:
                                event = _json.loads(raw_line)
                                if event.get("type") == "content_block_delta":
                                    token = str((event.get("delta") or {}).get("text") or "")
                                    if token:
                                        yield _sse(token)
                            except Exception:
                                continue
                yield "data: [DONE]\n\n"
                return
            except Exception as exc:
                logger.warning("Anthropic stream failed, falling back: %s", exc)

        # Fallback: full response then chunk by word (Gemini / error recovery)
        try:
            response_json = await self._call_model(model_name, body)
            full_text = self._extract_text(response_json)
        except Exception as exc:
            logger.exception("stream_response fallback call failed: %s", exc)
            yield "data: [DONE]\n\n"
            return

        words = full_text.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else " " + word
            yield _sse(chunk)
            await _asyncio.sleep(0.02)
        yield "data: [DONE]\n\n"

    def _extract_text(self, payload: Dict[str, Any]) -> str:
        provider = str(payload.get("_provider") or self._provider)
        try:
            if provider in {"openai", "ollama"}:
                choices = payload.get("choices", [])
                if not choices:
                    return ""
                return str((choices[0].get("message") or {}).get("content") or "")
            if provider == "anthropic":
                content = list(payload.get("content") or [])
                if not content:
                    return ""
                text_parts = [
                    str(item.get("text") or "")
                    for item in content
                    if isinstance(item, dict) and str(item.get("type") or "") == "text"
                ]
                return "\n".join(x for x in text_parts if x)

            candidates = payload.get("candidates", [])
            if not candidates:
                return ""
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return ""
            return parts[0].get("text", "") or ""
        except Exception:
            return ""

    def _extract_usage(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        provider = str(payload.get("_provider") or self._provider)
        if provider in {"openai", "ollama"}:
            usage = payload.get("usage")
            if not usage:
                return None
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
            total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens))
            cost = self._estimate_cost(input_tokens, output_tokens, provider=provider)
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost,
            }
        if provider == "anthropic":
            usage = payload.get("usage")
            if not usage:
                return None
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            total_tokens = input_tokens + output_tokens
            cost = self._estimate_cost(input_tokens, output_tokens, provider=provider)
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost,
            }

        usage = payload.get("usageMetadata")
        if not usage:
            return None
        input_tokens = int(usage.get("promptTokenCount", 0))
        output_tokens = int(usage.get("candidatesTokenCount", 0))
        total_tokens = int(usage.get("totalTokenCount", input_tokens + output_tokens))

        cost = self._estimate_cost(input_tokens, output_tokens, provider=provider)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }

    def _estimate_cost(self, input_tokens: int, output_tokens: int, *, provider: Optional[str] = None) -> Optional[float]:
        active_provider = str(provider or self._provider).lower()
        if active_provider == "ollama":
            return None  # local inference has no API cost
        if active_provider == "openai":
            in_rate = os.getenv("OPENAI_COST_PER_INPUT_TOKEN")
            out_rate = os.getenv("OPENAI_COST_PER_OUTPUT_TOKEN")
        elif active_provider == "anthropic":
            in_rate = os.getenv("ANTHROPIC_COST_PER_INPUT_TOKEN")
            out_rate = os.getenv("ANTHROPIC_COST_PER_OUTPUT_TOKEN")
        else:
            in_rate = os.getenv("GEMINI_COST_PER_INPUT_TOKEN")
            out_rate = os.getenv("GEMINI_COST_PER_OUTPUT_TOKEN")
        if not in_rate and not out_rate:
            return None
        input_cost = float(in_rate or 0) * input_tokens
        output_cost = float(out_rate or 0) * output_tokens
        return round(input_cost + output_cost, 6)

    async def _persist_conversation(
        self,
        session: AsyncSession,
        client_id: uuid.UUID,
        session_id: str,
        message: str,
        assistant_text: str,
    ) -> None:
        session.add_all(
            [
                ConversationLog(client_id=client_id, session_id=session_id, role="user", content=message),
                ConversationLog(client_id=client_id, session_id=session_id, role="assistant", content=assistant_text),
            ]
        )
        await session.commit()

    async def _persist_usage(
        self, session: AsyncSession, client_id: uuid.UUID, usage: Dict[str, Any]
    ) -> None:
        session.add(
            TokenUsage(
                client_id=client_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=usage.get("cost_usd"),
            )
        )
        await session.commit()

    def _is_sandbox_eligible(self, message: str) -> bool:
        """Heuristic: True when the message looks like an agent task.

        Activates the ReAct loop when:
        - Message mentions multi-step connectors ("and then", "luego"), OR
        - Message has 2+ distinct action verbs, OR
        - Message mentions a force-keyword (workflow, n8n, api, crm, etc.)
        """
        msg = message.lower().strip()
        if any(connector in msg for connector in _SANDBOX_CONNECTORS):
            return True
        if any(kw in msg for kw in _SANDBOX_FORCE_KEYWORDS):
            return True
        verb_count = sum(1 for verb in _SANDBOX_ACTION_VERBS if verb in msg)
        return verb_count >= 2

    def _sandbox_result_to_response(self, result: Any) -> Dict[str, Any]:
        """Convert a SandboxResult to the standard generate_response dict format."""
        content = (
            result.final_answer
            or f"[Sandbox: {result.stopped_reason} después de {result.iterations} iteración(es)]"
        )
        return {
            "type": "message",
            "content": content,
            "sandbox_run_id": result.run_id,
            "sandbox_committed": result.committed,
            "sandbox_pending_commit": result.pending_commit,
            "sandbox_iterations": result.iterations,
            "sandbox_stopped_reason": result.stopped_reason,
        }

    def _extract_action(self, text: str) -> Optional[Dict[str, Any]]:
        candidate = text.strip()
        if not candidate:
            return None

        if candidate.startswith("```"):
            candidate = candidate.strip("`\n ")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()

        if candidate.startswith("ACTION:"):
            candidate = candidate[len("ACTION:") :].strip()

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        if "contract_version" not in data:
            return None
        if "action" not in data:
            return None
        return data
