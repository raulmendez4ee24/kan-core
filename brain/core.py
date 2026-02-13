import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import Client, ConversationLog, TokenUsage
from observability import capture_exception
from tools.retry import request_with_retry

logger = logging.getLogger("kan_core.cognitive")

DEFAULT_SYSTEM_PROMPT = (
    "You are K'an Logic Systems, a precise and helpful AI assistant. "
    "Always be concise, accurate, and safe. If a request requires an external action "
    "(e.g., scheduling, checking stock, creating an order), respond ONLY with a JSON object "
    "containing keys: action, arguments, and rationale. Otherwise, respond normally in plain text."
)


class CognitiveEngine:
    _instance: Optional["CognitiveEngine"] = None

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._base_url = os.getenv(
            "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
        )
        self._timeout = float(os.getenv("GEMINI_TIMEOUT", "20"))
        self._retries = int(os.getenv("GEMINI_RETRIES", "3"))
        self._backoff_base = float(os.getenv("GEMINI_BACKOFF_BASE", "0.5"))
        self._backoff_max = float(os.getenv("GEMINI_BACKOFF_MAX", "8.0"))
        self._max_output_tokens = os.getenv("GEMINI_MAX_OUTPUT_TOKENS")

    @classmethod
    def get_instance(cls) -> "CognitiveEngine":
        if cls._instance is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            cls._instance = cls(api_key)
        return cls._instance

    async def generate_response(
        self, client_id: str, session_id: str, message: str
    ) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            return await self._generate_with_session(
                session, client_id=client_id, session_id=session_id, message=message
            )

    async def _generate_with_session(
        self, session: AsyncSession, *, client_id: str, session_id: str, message: str
    ) -> Dict[str, Any]:
        client_uuid = self._parse_client_id(client_id)
        client = await session.get(Client, client_uuid)
        if not client:
            raise ValueError("Client not found")

        system_prompt = client.system_prompt or DEFAULT_SYSTEM_PROMPT
        model_name = client.model_name or "gemini-1.5-flash"
        temperature = client.temperature if client.temperature is not None else 0.4

        history = await self._load_history(session, client_uuid, session_id)
        request_body = self._build_request(
            system_prompt=system_prompt,
            history=history,
            message=message,
            temperature=temperature,
        )

        try:
            response_json = await self._call_gemini(model_name, request_body)
        except Exception as exc:
            logger.exception("Gemini request failed")
            capture_exception(exc, {"source": "gemini", "client_id": client_id})
            raise

        assistant_text = self._extract_text(response_json)
        usage = self._extract_usage(response_json)

        await self._persist_conversation(
            session,
            client_uuid,
            session_id,
            message,
            assistant_text,
        )
        if usage:
            await self._persist_usage(session, client_uuid, usage)

        action = self._extract_action(assistant_text)
        if action:
            return {"type": "action", "payload": action, "content": assistant_text}
        return {"type": "message", "content": assistant_text}

    def _parse_client_id(self, client_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(client_id)
        except ValueError as exc:
            raise ValueError("client_id must be a UUID string") from exc

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

    def _build_request(
        self,
        *,
        system_prompt: str,
        history: List[ConversationLog],
        message: str,
        temperature: float,
    ) -> Dict[str, Any]:
        contents: List[Dict[str, Any]] = []
        for item in history:
            role = "user" if item.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": item.content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        generation_config: Dict[str, Any] = {"temperature": temperature}
        if self._max_output_tokens:
            generation_config["maxOutputTokens"] = int(self._max_output_tokens)

        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

    async def _call_gemini(self, model_name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url.rstrip('/')}/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": self._api_key}

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

    def _extract_text(self, payload: Dict[str, Any]) -> str:
        try:
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
        usage = payload.get("usageMetadata")
        if not usage:
            return None
        input_tokens = int(usage.get("promptTokenCount", 0))
        output_tokens = int(usage.get("candidatesTokenCount", 0))
        total_tokens = int(usage.get("totalTokenCount", input_tokens + output_tokens))

        cost = self._estimate_cost(input_tokens, output_tokens)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> Optional[float]:
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
                ConversationLog(
                    client_id=client_id,
                    session_id=session_id,
                    role="user",
                    content=message,
                ),
                ConversationLog(
                    client_id=client_id,
                    session_id=session_id,
                    role="assistant",
                    content=assistant_text,
                ),
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
        if isinstance(data, dict) and "action" in data:
            return data
        return None
