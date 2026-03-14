from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx

from database import AsyncSessionLocal

logger = logging.getLogger("kan_core.master_brain")

_WEB_URL_RE = re.compile(r"(https?://[^\s)]+|www\.[^\s)]+)", re.IGNORECASE)
_BUSINESS_HINTS = {
    "cliente",
    "clientes",
    "prospecto",
    "prospectos",
    "lead",
    "leads",
    "marketing",
    "campana",
    "campaña",
    "ventas",
    "crm",
    "embudo",
    "funnel",
    "conversion",
    "conversión",
    "negocio",
    "empresa",
}
_API_HINTS = {
    "api",
    "apis",
    "openapi",
    "swagger",
    "integracion",
    "integración",
    "integrar",
    "integra",
    "conectar api",
    "conecta api",
    "webhook",
    "oauth",
    "token",
    "sdk",
}
_DESKTOP_HINTS = {
    "abre",
    "abrir",
    "open",
    "click",
    "escribe",
    "tecla",
    "teclado",
    "pantalla",
    "desktop",
    "app",
    "aplicacion",
    "aplicación",
}
_CHAT_HINTS = {
    "ok",
    "okei",
    "entendido",
    "gracias",
    "perfecto",
    "listo",
    "sale",
    "jaja",
    "jajaja",
    "boludo",
    "wey",
    "wey.",
    "wtf",
    "hola",
    "buenas",
    "como estas",
    "cómo estás",
    "qué tal",
    "que tal",
    "como estas?",
    "qué tal?",
    "hi",
    "hello",
    "hey",
}
# Phrases that trigger web research; short inputs without these never call Tavily.
_WEB_RESEARCH_KEYWORDS = (
    "investiga",
    "investigar",
    "tendencias",
    "competencia",
    "research",
    "market research",
    "mercado",
    "análisis de",
    "analisis de",
)
# Substrings that signal a question about the bot's capabilities — always chat.
# "hacer" in _ACTION_VERBS would wrongly route "¿qué puedes hacer?" to MasterBrain.
_CAPABILITY_QUESTION_PHRASES = (
    "que puedes",
    "qué puedes",
    "que sabes",
    "qué sabes",
    "que haces",
    "qué haces",
    "como puedes",
    "cómo puedes",
    "puedes hacer",
    "puedes ayudar",
    "como funciona",
    "cómo funciona",
    "para que sirves",
    "para qué sirves",
    "que eres",
    "qué eres",
)
_ACTION_VERBS = {
    "abre",
    "abrir",
    "open",
    "buscame",
    "búscame",
    "busca",
    "buscar",
    "elimina",
    "eliminar",
    "borra",
    "borrar",
    "integra",
    "integrar",
    "conecta",
    "conectar",
    "genera",
    "generar",
    "crea",
    "crear",
    "haz",
    "hacer",
    "ejecuta",
    "ejecutar",
    "analiza",
    "analizar",
}
_DANGEROUS_KEYWORDS = {
    "delete",
    "eliminar",
    "borrar",
    "refund",
    "charge",
    "withdraw",
    "transfer",
    "payment",
    "credencial",
    "token",
    "secret",
}
_AUTONOMY_LEVELS = {"low", "medium", "high"}
_INTENTS = {"chat", "business", "web", "desktop", "api", "integrations", "mixed", "unknown"}
_EXECUTOR_TO_TOOL = {
    "autonomy_v2": "api",
    "n8n": "api",
    "web_agent": "browser",
    "desktop_loop": "desktop",
    "autonomous_agent": "desktop",
}


def _env_bool(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes"}


def _safe_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_jsonable(v) for v in value]
    return value


def _first_nonempty(*values: str) -> str:
    for value in values:
        probe = str(value or "").strip()
        if probe:
            return probe
    return ""


_SIMPLE_DESKTOP_RE = re.compile(
    r"^(abre?|open|cierra|close|lanza|inicia|cambia|activa|navega|switch|mueve|minimiza)\b",
    re.IGNORECASE,
)


def _is_simple_desktop_goal(goal: str) -> bool:
    """Short desktop command that needs no multi-step planning.

    Requires BOTH: starts with an unambiguous app verb AND is ≤6 words.
    Long goals (even starting with 'Abre') are NOT simple and need full planning.
    """
    probe = str(goal or "").strip()
    if not probe:
        return False
    words = probe.split()
    return bool(_SIMPLE_DESKTOP_RE.match(probe)) and len(words) <= 6


class MasterBrain:
    def __init__(
        self,
        *,
        client_id: str,
        model: Optional[str] = None,
        max_steps: int = 12,
        run_id: Optional[str] = None,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self.model = str(model or "").strip() or None
        self.max_steps = max(1, min(int(max_steps), 40))
        self.run_id = str(run_id or uuid4().hex[:8])
        level = str(os.getenv("AUTONOMY_LEVEL", "high")).strip().lower()
        self.autonomy_level = level if level in _AUTONOMY_LEVELS else "high"
        self._include_debug_steps = _env_bool("MASTER_BRAIN_INCLUDE_DEBUG_STEPS", "false")
        self._enable_learning = _env_bool("MASTER_BRAIN_ENABLE_LEARNING", "true")
        self._enable_world_model = _env_bool("MASTER_BRAIN_ENABLE_WORLD_MODEL", "true")
        self._enable_web_research = _env_bool("MASTER_BRAIN_ENABLE_WEB_RESEARCH", "true")

        from brain.continuous_improvement import run_continuous_optimization
        from brain.global_learning import refresh_global_learnings
        from brain.tool_selection import build_tool_selection_snapshot
        from learning.tool_memory import record_tool_outcome

        self._session_factory = AsyncSessionLocal
        self._continuous_optimizer = run_continuous_optimization
        self._global_refresh = refresh_global_learnings
        self._tool_outcome_recorder = record_tool_outcome
        self._tool_snapshot_builder = build_tool_selection_snapshot

    async def run(self, goal: str) -> Dict[str, Any]:
        goal_text = str(goal or "").strip()
        if not goal_text:
            return self._build_response(
                status="failed",
                summary="No recibí un objetivo válido.",
                data={},
                iterations=0,
                steps=[],
                needs_confirmation=False,
                confirmation_reason=None,
            )

        logger.info("[MASTER_BRAIN] INPUT goal=%r run_id=%s", goal_text[:180], self.run_id)
        pipeline_steps: List[Dict[str, Any]] = []
        context: Dict[str, Any] = {}
        strategy: Dict[str, Any] = {}
        plan: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []

        try:
            hinted_intent = self._infer_intent_heuristic(goal_text)
            if _env_bool("ENABLE_AUTONOMOUS_CASE_MANAGER", "false"):
                org_id = self._parse_client_uuid(self.client_id)
                if org_id and hinted_intent != "chat":
                    from brain.autonomous_case_manager import (
                        classify_case_type,
                        create_or_update_case as create_autonomous_case,
                    )

                    case_type = classify_case_type(
                        current_goal=goal_text,
                        business_context={"source": "master_brain", "intent_hint": hinted_intent},
                        requested_case_type=None,
                        allow_generic=False,
                    )
                    if case_type:
                        async with self._session_factory() as session:
                            case_result = await create_autonomous_case(
                                session,
                                organization_id=org_id,
                                case_type=case_type,
                                current_goal=goal_text,
                                business_context={"source": "master_brain", "intent_hint": hinted_intent},
                                run_now=True,
                            )
                        run_payload = dict(case_result.get("run") or {})
                        return self._build_response(
                            status=str((run_payload.get("case") or {}).get("status") or "completed"),
                            summary="Objetivo delegado al supervisor operativo autonomo.",
                            data={
                                "case": case_result.get("case"),
                                "decision": run_payload.get("decision", {}),
                                "execution_result": run_payload.get("execution_result", {}),
                                "supervision": run_payload.get("supervision", {}),
                            },
                            iterations=1,
                            steps=(pipeline_steps if self._include_debug_steps else []),
                            needs_confirmation=False,
                            confirmation_reason=None,
                            runtime="autonomous_case_manager",
                        )
            # Conversation Fast Path: chat/smalltalk → modelo fuerte, sin _load_context, sin Tavily/tools.
            if hinted_intent == "chat":
                logger.info("[MASTER_BRAIN] CONVERSATION_FAST_PATH hinted_intent=chat run_id=%s", self.run_id)
                summary = await self._chat_response(goal_text)
                return self._build_response(
                    status="completed",
                    summary=summary,
                    data={
                        "intent": "chat",
                        "autonomy_level_applied": self.autonomy_level,
                        "risk_bypassed": self.autonomy_level == "high",
                    },
                    iterations=0,
                    steps=(pipeline_steps if self._include_debug_steps else []),
                    needs_confirmation=False,
                    confirmation_reason=None,
                    runtime="master_brain",
                )

            context = await self._load_context(goal_text, hinted_intent=hinted_intent)
            intent = await self._infer_intent(
                goal_text,
                context,
                hinted_intent=hinted_intent,
            )
            context["intent"] = intent
            pipeline_steps.append(
                {"stage": "contextual_reasoning", "status": "ok", "intent": intent}
            )
            logger.info("[MASTER_BRAIN] CONTEXT_READY intent=%s run_id=%s", intent, self.run_id)

            if intent == "chat":
                summary = await self._chat_response(goal_text)
                return self._build_response(
                    status="completed",
                    summary=summary,
                    data={
                        "intent": intent,
                        "autonomy_level_applied": self.autonomy_level,
                        "risk_bypassed": self.autonomy_level == "high",
                    },
                    iterations=0,
                    steps=(pipeline_steps if self._include_debug_steps else []),
                    needs_confirmation=False,
                    confirmation_reason=None,
                    runtime="master_brain",
                )

            # Fast-path: simple desktop goals (≤5 words / starts with app verb) skip
            # StrictPlanner entirely and go straight to the desktop executor.
            if intent == "desktop" and _is_simple_desktop_goal(goal_text):
                pipeline_steps.append({"stage": "desktop_fast_path", "status": "ok"})
                logger.info(
                    "[MASTER_BRAIN] DESKTOP_FAST_PATH goal=%r run_id=%s",
                    goal_text[:80],
                    self.run_id,
                )
                context["intent"] = intent
                exec_out = await self._execute_desktop_loop(goal_text)
                if exec_out.get("status") == "failed":
                    exec_out = await self._execute_autonomous_agent(goal_text)
                fast_results = [
                    {
                        "step_id": "step-1",
                        "executor": "desktop_loop",
                        "status": exec_out.get("status", "failed"),
                        "summary": exec_out.get("summary", ""),
                        "error": exec_out.get("error", ""),
                        "data": _safe_jsonable(exec_out.get("data") or {}),
                        "duration_ms": 0,
                        "risk": "medium",
                    }
                ]
                evaluation = self._evaluate_and_summarize(
                    goal=goal_text,
                    context=context,
                    strategy={},
                    results=fast_results,
                )
                learning = await self._learn(goal_text, context, fast_results)
                return self._build_response(
                    status=str(evaluation.get("status") or "failed"),
                    summary=str(
                        evaluation.get("summary") or exec_out.get("summary") or ""
                    ),
                    data={
                        "intent": intent,
                        "execution_results": _safe_jsonable(fast_results),
                        "evaluation": _safe_jsonable(evaluation),
                        "learning": _safe_jsonable(learning),
                        "autonomy_level_applied": self.autonomy_level,
                        "risk_bypassed": self.autonomy_level == "high",
                        "data": _safe_jsonable(evaluation.get("data") or {}),
                    },
                    iterations=1,
                    steps=(pipeline_steps if self._include_debug_steps else []),
                    needs_confirmation=False,
                    confirmation_reason=None,
                    runtime="desktop_autonomy",
                )

            strategy = await self._generate_strategy(goal_text, context, intent)
            pipeline_steps.append({"stage": "strategy_generation", "status": "ok"})
            logger.info("[MASTER_BRAIN] STRATEGY_READY run_id=%s", self.run_id)

            plan = self._build_execution_plan(strategy, context)
            pipeline_steps.append(
                {"stage": "execution_plan", "status": "ok", "step_count": len(plan)}
            )
            logger.info("[MASTER_BRAIN] EXEC_PLAN_READY steps=%d run_id=%s", len(plan), self.run_id)

            routing = await self._choose_tools(plan, context)
            decision = self._decide_user_input_needed(plan, self.autonomy_level)

            if decision.get("needs_input"):
                summary = _first_nonempty(
                    str(decision.get("reason") or ""),
                    "Necesito confirmación para proceder con la ejecución.",
                )
                return self._build_response(
                    status="blocked",
                    summary=summary,
                    data={
                        "intent": intent,
                        "strategy": _safe_jsonable(strategy),
                        "execution_plan": _safe_jsonable(plan),
                        "tool_routing": _safe_jsonable(routing),
                        "autonomy_level_applied": self.autonomy_level,
                        "risk_bypassed": self.autonomy_level == "high",
                    },
                    iterations=0,
                    steps=(pipeline_steps if self._include_debug_steps else []),
                    needs_confirmation=True,
                    confirmation_reason=summary,
                    runtime="master_brain",
                )

            if self.autonomy_level == "low":
                summary = self._strategy_summary(goal_text, context, strategy)
                return self._build_response(
                    status="dry_run",
                    summary=summary,
                    data={
                        "intent": intent,
                        "strategy": _safe_jsonable(strategy),
                        "execution_plan": _safe_jsonable(plan),
                        "tool_routing": _safe_jsonable(routing),
                        "autonomy_level_applied": self.autonomy_level,
                        "risk_bypassed": False,
                    },
                    iterations=len(plan),
                    steps=(pipeline_steps if self._include_debug_steps else []),
                    needs_confirmation=False,
                    confirmation_reason=None,
                    runtime="master_brain",
                )

            results = await self._execute_plan(plan, routing, context)
            pipeline_steps.append(
                {
                    "stage": "action",
                    "status": "ok",
                    "executed": len(results),
                }
            )

            evaluation = self._evaluate_and_summarize(
                goal=goal_text,
                context=context,
                strategy=strategy,
                results=results,
            )
            pipeline_steps.append({"stage": "evaluation", "status": "ok"})
            logger.info(
                "[MASTER_BRAIN] EVAL status=%s run_id=%s",
                evaluation.get("status"),
                self.run_id,
            )

            learning = await self._learn(goal_text, context, results)
            pipeline_steps.append({"stage": "learning", "status": "ok"})
            logger.info("[MASTER_BRAIN] LEARNING run_id=%s", self.run_id)

            data = {
                "intent": context.get("intent"),
                "strategy": _safe_jsonable(strategy),
                "execution_plan": _safe_jsonable(plan),
                "tool_routing": _safe_jsonable(routing),
                "execution_results": _safe_jsonable(results),
                "evaluation": _safe_jsonable(evaluation),
                "learning": _safe_jsonable(learning),
                "autonomy_level_applied": self.autonomy_level,
                "risk_bypassed": self.autonomy_level == "high",
                "data": _safe_jsonable(evaluation.get("data") or {}),
            }
            return self._build_response(
                status=str(evaluation.get("status") or "failed"),
                summary=str(evaluation.get("summary") or "No pude completar el objetivo."),
                data=data,
                iterations=len(results),
                steps=(pipeline_steps if self._include_debug_steps else []),
                needs_confirmation=False,
                confirmation_reason=None,
                runtime=self._derive_runtime(results),
            )
        except Exception as exc:
            logger.exception("[MASTER_BRAIN] pipeline failed run_id=%s", self.run_id)
            return self._build_response(
                status="failed",
                summary=f"Fallo interno en MasterBrain: {exc}",
                data={
                    "intent": context.get("intent"),
                    "strategy": _safe_jsonable(strategy),
                    "execution_plan": _safe_jsonable(plan),
                    "execution_results": _safe_jsonable(results),
                    "autonomy_level_applied": self.autonomy_level,
                    "risk_bypassed": self.autonomy_level == "high",
                },
                iterations=len(results),
                steps=(pipeline_steps if self._include_debug_steps else []),
                needs_confirmation=False,
                confirmation_reason=None,
                runtime="master_brain",
            )

    async def _load_context(self, goal: str, *, hinted_intent: str = "unknown") -> Dict[str, Any]:
        from brain.context_engine import ContextItem, compact_context
        from brain.specialist_memory import specialist_memory

        context: Dict[str, Any] = {
            "goal": goal,
            "memory_chunks": [],
            "specialist_memory": {},
            "business_analysis": {},
            "world_model_transitions": [],
            "compact_context": [],
            "web_research_notes": [],
        }

        memory_chunks: List[str] = []
        try:
            from brain.core import CognitiveEngine

            engine = CognitiveEngine.get_instance()
            memory_backend = getattr(engine, "_memory", None)
            if memory_backend is not None and hasattr(memory_backend, "search_context"):
                rows = await memory_backend.search_context(
                    goal,
                    top_k=5,
                    filters={"client_id": self.client_id},
                )
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            snippet = str(
                                row.get("content")
                                or row.get("text")
                                or row.get("summary")
                                or ""
                            ).strip()
                        else:
                            snippet = str(row or "").strip()
                        if snippet:
                            memory_chunks.append(snippet[:500])
        except Exception:
            memory_chunks = []
        context["memory_chunks"] = memory_chunks

        try:
            recall = specialist_memory.recall(
                client_id=self.client_id,
                specialist="master_brain",
                goal=goal,
                max_items=6,
            )
            context["specialist_memory"] = recall
        except Exception:
            context["specialist_memory"] = {}

        org_id = self._parse_client_uuid(self.client_id)
        if org_id is not None:
            async with self._session_factory() as session:
                try:
                    from brain.business_context_engine import analyze_business_context

                    business_analysis = await analyze_business_context(
                        session,
                        organization_id=org_id,
                        window_hours=max(
                            6, int(str(os.getenv("MASTER_BRAIN_CONTEXT_WINDOW_HOURS") or "24"))
                        ),
                    )
                    context["business_analysis"] = _safe_jsonable(business_analysis)
                except Exception:
                    context["business_analysis"] = {}

                if self._enable_world_model:
                    try:
                        from brain.world_model import best_transitions_for_state

                        transitions = await best_transitions_for_state(
                            session,
                            organization_id=org_id,
                            from_signature=goal[:180],
                            limit=5,
                        )
                        context["world_model_transitions"] = [
                            {
                                "action": str(getattr(item, "action", "") or ""),
                                "success_rate": float(getattr(item, "success_rate", 0.0) or 0.0),
                                "to_signature": str(getattr(item, "to_signature", "") or ""),
                                "sample_size": int(getattr(item, "sample_size", 0) or 0),
                            }
                            for item in transitions
                        ]
                    except Exception:
                        context["world_model_transitions"] = []

        # Web research only for real research requests: long goal or explicit keywords.
        # Never for chat/smalltalk/desktop or short inputs (avoids Tavily on "Hola" etc).
        goal_lower = (goal or "").strip().lower()
        want_web_research = (
            self._enable_web_research
            and not memory_chunks
            and hinted_intent not in {"chat", "smalltalk", "desktop"}
            and (len(goal or "") >= 40 or any(kw in goal_lower for kw in _WEB_RESEARCH_KEYWORDS))
        )
        if want_web_research:
            context["web_research_notes"] = await self._quick_web_research(goal)

        semantic_chunks = list(memory_chunks)
        episodic_chunks: List[str] = []
        task_chunks: List[str] = []
        if isinstance(context.get("specialist_memory"), dict):
            episodic_chunks = [
                str(item.get("goal") or "")
                for item in list(context["specialist_memory"].get("episodic") or [])
                if isinstance(item, dict)
            ]
            task_chunks = [
                str(item.get("goal") or "")
                for item in list(context["specialist_memory"].get("task") or [])
                if isinstance(item, dict)
            ]
        items: List[ContextItem] = [
            ContextItem(text=text, success_score=0.6) for text in semantic_chunks if text
        ]
        if items:
            context["compact_context"] = compact_context(
                query=goal,
                semantic_chunks=semantic_chunks,
                episodic_chunks=episodic_chunks,
                task_chunks=task_chunks,
                max_chars=1200,
            )
        return context

    async def _quick_web_research(self, goal: str) -> List[str]:
        notes: List[str] = []
        try:
            from brain.autonomy_web_research import _search_brave, _search_tavily

            tavily = await _search_tavily(goal, max_results=2)
            if tavily:
                notes.extend(
                    [
                        _first_nonempty(
                            str(item.title or "").strip(),
                            str(item.snippet or "").strip(),
                        )
                        for item in tavily[:2]
                    ]
                )
            if not notes:
                brave = await _search_brave(goal, max_results=2)
                if brave:
                    notes.extend(
                        [
                            _first_nonempty(
                                str(item.title or "").strip(),
                                str(item.snippet or "").strip(),
                            )
                            for item in brave[:2]
                        ]
                    )
        except Exception:
            return []
        return [n for n in notes if n][:3]

    async def _infer_intent(
        self,
        goal: str,
        context: Dict[str, Any],
        *,
        hinted_intent: str = "unknown",
    ) -> str:
        if hinted_intent in _INTENTS and hinted_intent != "unknown":
            return hinted_intent
        llm_intent = await self._infer_intent_with_llm(goal, context)
        if llm_intent in _INTENTS and llm_intent != "unknown":
            return llm_intent
        return self._infer_intent_heuristic(goal, context)

    def _infer_intent_heuristic(self, goal: str, context: Optional[Dict[str, Any]] = None) -> str:
        probe = str(goal or "").lower()
        stripped = re.sub(r"[^\wáéíóúñü\s]", " ", probe)
        tokens = {tok for tok in stripped.split() if tok}
        has_action_verb = any(token in tokens for token in _ACTION_VERBS)
        has_chat_hint = any(token in probe for token in _CHAT_HINTS)
        short_message = len(tokens) <= 5
        has_digits = any(ch.isdigit() for ch in probe)
        # "¿Qué puedes hacer?" / "Cómo funciona?" — questions about capabilities are always chat.
        if any(phrase in probe for phrase in _CAPABILITY_QUESTION_PHRASES):
            return "chat"
        if has_chat_hint and short_message and not has_action_verb:
            return "chat"
        if short_message and not has_action_verb and not has_digits:
            has_other_intent_signal = any(
                token in probe for token in (_BUSINESS_HINTS | _DESKTOP_HINTS | _API_HINTS)
            ) or bool(_WEB_URL_RE.search(probe))
            if not has_other_intent_signal:
                return "chat"
        if has_chat_hint and not has_action_verb and not bool(_WEB_URL_RE.search(probe)):
            return "chat"

        has_api = any(token in probe for token in _API_HINTS)
        has_web = bool(_WEB_URL_RE.search(probe)) or any(
            token in probe
            for token in ("web", "sitio", "página", "pagina", "formulario", "browser", "url")
        )
        has_business = any(token in probe for token in _BUSINESS_HINTS)
        has_desktop = any(token in probe for token in _DESKTOP_HINTS)
        if has_api and has_web:
            return "integrations"
        if has_api:
            return "api"
        if has_business and has_web:
            return "mixed"
        if has_business:
            return "business"
        if has_web:
            return "web"
        if has_desktop:
            return "desktop"
        # Intent gate: only promote to "business" from context when goal has real signals (BUSINESS_HINTS or ACTION_VERBS).
        # "Hola" with business_analysis in context must NOT become business.
        if isinstance(context, dict) and context.get("business_analysis") and (has_business or has_action_verb):
            return "business"
        return "unknown"

    async def _infer_intent_with_llm(self, goal: str, context: Dict[str, Any]) -> str:
        if not _env_bool("MASTER_BRAIN_ENABLE_INTENT_LLM", "true"):
            return "unknown"
        api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return "unknown"
        try:
            model = str(
                self.model
                or os.getenv("OPENAI_FAST_MODEL")
                or os.getenv("OPENAI_MODEL")
                or "gpt-4o-mini"
            ).strip()
            payload = {
                "goal": goal[:240],
                "hints": {
                    "has_url": bool(_WEB_URL_RE.search(goal)),
                    "has_business": any(token in goal.lower() for token in _BUSINESS_HINTS),
                    "has_api": any(token in goal.lower() for token in _API_HINTS),
                },
                "allowed_intents": sorted(_INTENTS),
                "business_context_present": bool(context.get("business_analysis")),
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "temperature": 0.0,
                        "max_tokens": 80,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Classify intent. Return JSON: {\"intent\":\"...\"}. "
                                    "Prefer 'chat' for acknowledgements/smalltalk/insults without action request."
                                ),
                            },
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                    },
                )
                if resp.status_code != 200:
                    return "unknown"
                content = (
                    resp.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                parsed = json.loads(str(content or "{}"))
                intent = str(parsed.get("intent") or "").strip().lower()
                if intent in _INTENTS:
                    return intent
        except Exception:
            return "unknown"
        return "unknown"

    async def _generate_strategy(
        self,
        goal: str,
        context: Dict[str, Any],
        intent: str,
    ) -> Dict[str, Any]:
        from brain.mission_planner import build_mission_steps

        recommendations: List[Dict[str, Any]] = []
        business_analysis = context.get("business_analysis")
        if isinstance(business_analysis, dict):
            insights = list(business_analysis.get("insights") or [])
            for insight in insights[:5]:
                if not isinstance(insight, dict):
                    continue
                recommendations.append(
                    {
                        "proposal": str(
                            insight.get("reason")
                            or insight.get("key")
                            or "Aplicar estrategia de crecimiento"
                        ),
                        "request_text": f"{goal}. {str(insight.get('reason') or '').strip()}",
                        "desired_functions": ["crm", "messaging"],
                    }
                )

        industry = self._infer_industry(goal, context)
        mission_steps = build_mission_steps(
            goal=goal,
            recommendations=recommendations,
            industry=industry,
        )

        strict_actions: List[Dict[str, Any]] = []
        strict_payload: Dict[str, Any] = {}
        strict_error = ""
        if _env_bool("MASTER_BRAIN_USE_STRICT_PLANNER", "true") and intent not in {"desktop"}:
            try:
                from brain.core import CognitiveEngine
                from brain.strict_planner import StrictPlanner

                planner = StrictPlanner(max_retries=2)
                engine = CognitiveEngine.get_instance()
                strict_payload = await planner.build_plan(
                    engine=engine,
                    client_id=self.client_id,
                    session_id=f"master_brain:{self.run_id}",
                    goal=goal,
                    context={
                        "intent": intent,
                        "compact_context": context.get("compact_context", []),
                        "web_research_notes": context.get("web_research_notes", []),
                    },
                )
                strict_plan = strict_payload.get("plan")
                if isinstance(strict_plan, dict):
                    strict_actions = [
                        item
                        for item in list(strict_plan.get("actions") or [])
                        if isinstance(item, dict)
                    ]
            except Exception as exc:
                strict_error = str(exc)

        if not strict_actions and _env_bool("MASTER_BRAIN_USE_ADAPTIVE_FALLBACK", "true"):
            org_id = self._parse_client_uuid(self.client_id)
            if org_id is not None:
                try:
                    from brain.adaptive_planner import AdaptivePlanner

                    async with self._session_factory() as session:
                        repl = await AdaptivePlanner.replan(
                            client_id=self.client_id,
                            session_id=f"master_brain:{self.run_id}",
                            goal=goal,
                            failed_steps=[],
                            failure_reason=_first_nonempty(
                                strict_error, "strict planner unavailable"
                            ),
                            current_tool="api",
                            db_session=session,
                        )
                    plan_candidate = repl.get("plan") if isinstance(repl, dict) else {}
                    if isinstance(plan_candidate, dict):
                        strict_actions = [
                            item
                            for item in list(plan_candidate.get("actions") or [])
                            if isinstance(item, dict)
                        ]
                except Exception:
                    pass

        objectives = [
            f"Resolver objetivo principal: {goal}",
            "Maximizar autonomía sin pedir información inicial",
        ]
        if intent in {"business", "mixed"}:
            objectives.append("Generar acciones de crecimiento comercial accionables")
        if intent in {"web", "mixed"}:
            objectives.append("Completar navegación/acciones web multi-paso")

        return {
            "intent": intent,
            "industry": industry,
            "objectives": objectives,
            "recommendations": recommendations,
            "mission_steps": mission_steps,
            "strict_plan": strict_payload,
            "strict_actions": strict_actions,
        }

    def _infer_industry(self, goal: str, context: Dict[str, Any]) -> str:
        probe = str(goal or "").lower()
        alias = {
            "restaurant": "restaurants",
            "restaurante": "restaurants",
            "clinica": "clinics",
            "clínica": "clinics",
            "gym": "gyms",
            "gimnasio": "gyms",
            "ecommerce": "ecommerce",
            "shopify": "ecommerce",
            "inmobiliaria": "real_estate",
            "real estate": "real_estate",
        }
        for token, normalized in alias.items():
            if token in probe:
                return normalized
        analysis = context.get("business_analysis")
        if isinstance(analysis, dict):
            insights = list(analysis.get("insights") or [])
            for item in insights:
                value = str(item.get("reason") or "").lower() if isinstance(item, dict) else ""
                for token, normalized in alias.items():
                    if token in value:
                        return normalized
        return "general"

    def _build_execution_plan(
        self,
        strategy: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        plan: List[Dict[str, Any]] = []
        strict_actions = [
            item for item in list(strategy.get("strict_actions") or []) if isinstance(item, dict)
        ]
        for idx, action in enumerate(strict_actions, start=1):
            action_type = str(action.get("type") or "analysis").strip().lower()
            raw_action = str(action.get("action") or "").strip()
            args = dict(action.get("params") or {})
            if isinstance(action.get("url"), str) and action.get("url"):
                args.setdefault("url", str(action.get("url")))
            title = _first_nonempty(
                str(action.get("description") or ""),
                raw_action,
                action_type,
            )
            normalized_action_type = self._normalize_action_type(action_type, args, context)
            risk = self._risk_level_for_action(
                action_type=normalized_action_type,
                action=raw_action,
                args=args,
            )
            plan.append(
                {
                    "id": f"step-{idx}",
                    "title": title[:180] or f"Step {idx}",
                    "tool": "auto",
                    "action_type": normalized_action_type,
                    "action": raw_action,
                    "args": args,
                    "risk": risk,
                    "success_criteria": f"Completar: {title[:120]}",
                }
            )
            if len(plan) >= self.max_steps:
                break

        if not plan:
            mission_steps = [
                item for item in list(strategy.get("mission_steps") or []) if isinstance(item, dict)
            ]
            for idx, mission in enumerate(mission_steps, start=1):
                action_type = "web" if context.get("intent") == "web" else "analysis"
                args = {
                    "goal": str(mission.get("request_text") or "").strip(),
                    "desired_functions": list(mission.get("desired_functions") or []),
                }
                risk = self._risk_level_for_action(
                    action_type=action_type,
                    action=str(mission.get("title") or ""),
                    args=args,
                )
                plan.append(
                    {
                        "id": f"step-{idx}",
                        "title": str(mission.get("title") or f"Step {idx}")[:180],
                        "tool": "auto",
                        "action_type": action_type,
                        "action": str(mission.get("step_type") or "mission"),
                        "args": args,
                        "risk": risk,
                        "success_criteria": f"Completar: {str(mission.get('title') or '')[:120]}",
                    }
                )
                if len(plan) >= self.max_steps:
                    break

        if not plan:
            default_type = "web" if context.get("intent") == "web" else "analysis"
            plan = [
                {
                    "id": "step-1",
                    "title": "Resolver objetivo principal",
                    "tool": "auto",
                    "action_type": default_type,
                    "action": "execute_goal",
                    "args": {"goal": context.get("goal")},
                    "risk": self._risk_level_for_action(
                        action_type=default_type,
                        action="execute_goal",
                        args={"goal": context.get("goal")},
                    ),
                    "success_criteria": "Entregar resultado verificable.",
                }
            ]

        return plan[: self.max_steps]

    def _normalize_action_type(
        self,
        action_type: str,
        args: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        token = str(action_type or "").strip().lower()
        if token in {"browser", "web", "browser_goto", "browser_click", "browser_fill"}:
            return "web"
        if token in {"desktop", "desktop_click", "desktop_type"}:
            return "desktop"
        if token in {"discover_apis", "discover"}:
            if self._allow_api_discovery(context=context, args=args):
                return "discover_apis"
            return "analysis"
        if token == "wait":
            return "wait"
        if token in {"analyze", "integration", "recommendation", "automation"}:
            return "analysis"
        if _WEB_URL_RE.search(str(args.get("url") or "")):
            return "web"
        if context.get("intent") == "desktop":
            return "desktop"
        return "analysis"

    def _allow_api_discovery(self, *, context: Dict[str, Any], args: Dict[str, Any]) -> bool:
        intent = str(context.get("intent") or "").strip().lower()
        if intent in {"api", "integrations"}:
            return True
        goal_probe = str(context.get("goal") or "").lower()
        if "conectar api" in goal_probe or "conecta api" in goal_probe:
            return True
        url_probe = str(args.get("url") or "").strip().lower()
        if url_probe and self._looks_like_api_docs_url(url_probe):
            return True
        return False

    def _looks_like_api_docs_url(self, url: str) -> bool:
        probe = str(url or "").lower()
        if not probe:
            return False
        api_markers = (
            "/openapi",
            "/swagger",
            "/api-docs",
            "/docs/api",
            "/reference",
            "/developers",
            "/graphql",
            "/v1",
            "/v2",
            "api.",
        )
        return any(marker in probe for marker in api_markers)

    async def _choose_tools(
        self,
        plan: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        from brain.tool_reasoner import rank_tools

        selected_tool = "api"
        if context.get("intent") == "web":
            selected_tool = "browser"
        elif context.get("intent") == "desktop":
            selected_tool = "desktop"

        ranked_tools: List[Dict[str, Any]] = []
        org_id = self._parse_client_uuid(self.client_id)
        if org_id is not None:
            try:
                async with self._session_factory() as session:
                    snapshot = await self._tool_snapshot_builder(
                        session,
                        organization_id=org_id,
                        window_days=30,
                        max_samples=120,
                    )
                selected_tool = str(snapshot.get("selected_tool") or selected_tool)
                candidates = [
                    {
                        "tool": str(item.get("tool") or ""),
                        "available": bool(item.get("available", True)),
                        "confidence": float(item.get("success_prob") or 0.0),
                        "health": 0.9 if item.get("available") else 0.0,
                        "cost": float(item.get("avg_cost_usd") or 0.05),
                        "compatibility": 0.8,
                        "speed": 0.7,
                        "history": min(1.0, float(item.get("sample_size") or 0.0) / 50.0),
                    }
                    for item in list(snapshot.get("tools") or [])
                    if isinstance(item, dict)
                ]
                ranked_tools = rank_tools(candidates=candidates, context={"max_cost": 1.0})
                if ranked_tools:
                    top_tool = str(ranked_tools[0].get("tool") or "").strip().lower()
                    if top_tool:
                        selected_tool = top_tool
            except Exception:
                pass

        routes: Dict[str, str] = {}
        for step in plan:
            step_id = str(step.get("id") or "")
            routes[step_id] = self._route_executor(
                step=step,
                selected_tool=selected_tool,
                intent=str(context.get("intent") or "unknown"),
            )

        return {
            "selected_tool": selected_tool,
            "ranked_tools": ranked_tools,
            "routes": routes,
        }

    def _route_executor(self, *, step: Dict[str, Any], selected_tool: str, intent: str) -> str:
        action_type = str(step.get("action_type") or "").lower()
        if action_type == "web":
            return "web_agent"
        if action_type == "desktop":
            return "desktop_loop" if selected_tool == "desktop" else "autonomous_agent"
        if action_type == "discover_apis":
            return "n8n" if selected_tool == "api" else "autonomous_agent"
        if action_type == "wait":
            return "autonomous_agent"
        if selected_tool == "browser":
            return "web_agent"
        if selected_tool == "desktop":
            return "autonomous_agent"
        if intent in {"business", "mixed"}:
            return "autonomy_v2"
        return "n8n"

    def _decide_user_input_needed(
        self,
        plan: List[Dict[str, Any]],
        autonomy_level: str,
    ) -> Dict[str, Any]:
        if autonomy_level == "medium":
            return {
                "needs_input": True,
                "reason": "AUTONOMY_LEVEL=medium requiere confirmación antes de ejecutar.",
            }
        return {"needs_input": False, "reason": ""}

    async def _execute_plan(
        self,
        plan: List[Dict[str, Any]],
        routing: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        routes = routing.get("routes") if isinstance(routing, dict) else {}
        if not isinstance(routes, dict):
            routes = {}
        results: List[Dict[str, Any]] = []
        for idx, step in enumerate(plan, start=1):
            if idx > self.max_steps:
                break
            step_id = str(step.get("id") or f"step-{idx}")
            executor = str(routes.get(step_id) or "n8n")
            start = time.monotonic()
            logger.info(
                "[MASTER_BRAIN] EXEC_STEP step_id=%s executor=%s run_id=%s",
                step_id,
                executor,
                self.run_id,
            )
            out = await self._execute_step(executor, step, context)
            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "step_id": step_id,
                "executor": executor,
                "status": str(out.get("status") or "failed"),
                "summary": str(out.get("summary") or ""),
                "error": str(out.get("error") or ""),
                "data": _safe_jsonable(out.get("data") or {}),
                "duration_ms": duration_ms,
                "risk": str(step.get("risk") or "low"),
            }
            results.append(result)
            if str(result["status"]) == "failed" and _env_bool("MASTER_BRAIN_STOP_ON_FAILURE", "false"):
                break
        return results

    async def _execute_step(
        self,
        executor: str,
        step: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        step_goal = self._step_goal(step, context)
        try:
            if executor == "web_agent":
                return await self._execute_web_agent(step_goal)
            if executor == "autonomy_v2":
                return await self._execute_autonomy_v2(step_goal)
            if executor == "autonomous_agent":
                return await self._execute_autonomous_agent(step_goal)
            if executor == "desktop_loop":
                return await self._execute_desktop_loop(step_goal)
            if executor == "n8n":
                return await self._execute_n8n(step)
            return {"status": "failed", "summary": "", "error": f"Unknown executor: {executor}", "data": {}}
        except Exception as exc:
            return {"status": "failed", "summary": "", "error": str(exc), "data": {}}

    def _step_goal(self, step: Dict[str, Any], context: Dict[str, Any]) -> str:
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        return _first_nonempty(
            str(args.get("goal") or ""),
            str(args.get("url") or ""),
            str(step.get("title") or ""),
            str(context.get("goal") or ""),
        )

    async def _execute_web_agent(self, goal: str) -> Dict[str, Any]:
        from browser_agent.web_agent_loop import WebAgentLoop

        agent = WebAgentLoop(
            max_steps=max(2, min(int(os.getenv("MASTER_BRAIN_WEB_MAX_STEPS", "6")), 20)),
            action_timeout_s=max(
                2.0, float(str(os.getenv("MASTER_BRAIN_WEB_TIMEOUT_SEC") or "12"))
            ),
            model=self.model,
        )
        start_url = ""
        url_match = _WEB_URL_RE.search(str(goal or ""))
        if url_match:
            start_url = str(url_match.group(1) or "").strip().rstrip(".,;")
            if start_url.lower().startswith("www."):
                start_url = "https://" + start_url

        result = await agent.run(goal, start_url=start_url or None)
        success = bool(result.get("success"))
        return {
            "status": "completed" if success else "failed",
            "summary": str(result.get("summary") or ""),
            "error": "",
            "data": _safe_jsonable(result.get("data") or {}),
        }

    async def _execute_autonomy_v2(self, goal: str) -> Dict[str, Any]:
        from brain.autonomy_v2_engine import run_autonomy_v2
        from schemas.autonomy_v2 import AutonomyV2RunRequest

        execution_mode = "autonomous" if self.autonomy_level == "high" else "balanced"
        async with self._session_factory() as session:
            req = AutonomyV2RunRequest(
                goal=goal,
                max_steps=max(2, min(self.max_steps, 10)),
                execution_mode=execution_mode,
                enable_learning=self._enable_learning,
                auto_optimize=True,
                context={"source": "master_brain", "run_id": self.run_id},
            )
            response = await run_autonomy_v2(
                client_id=self.client_id,
                req=req,
                session=session,
            )
        status = "completed" if response.status in {"completed", "dry_run"} else response.status
        return {
            "status": status,
            "summary": str(response.summary or ""),
            "error": str(response.error or ""),
            "data": {
                "benchmark": _safe_jsonable(response.benchmark),
                "quality": _safe_jsonable(response.quality),
                "next_actions": _safe_jsonable(response.next_actions),
            },
        }

    async def _execute_autonomous_agent(self, goal: str) -> Dict[str, Any]:
        from brain.autonomous_agent import AutonomousAgent

        execution_mode = "autonomous" if self.autonomy_level == "high" else "safe"
        async with AutonomousAgent(
            client_id=self.client_id,
            execution_mode=execution_mode,
        ) as agent:
            result = await agent.run_autonomous_loop(
                goal,
                max_iterations=max(2, min(self.max_steps, 10)),
                context={"source": "master_brain", "run_id": self.run_id},
            )
        status = str(result.get("status") or "failed").strip().lower()
        return {
            "status": "completed" if status == "completed" else status,
            "summary": str(result.get("summary") or ""),
            "error": str(result.get("error") or ""),
            "data": _safe_jsonable(result),
        }

    async def _execute_desktop_loop(self, goal: str) -> Dict[str, Any]:
        from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
        from schemas.desktop_autonomy import DesktopAutonomyRunRequest

        exec_mode = "autonomous" if self.autonomy_level == "high" else "balanced"
        allow_high_risk = self.autonomy_level == "high"
        async with self._session_factory() as session:
            req = DesktopAutonomyRunRequest(
                goal=goal,
                max_steps=max(2, min(self.max_steps, 8)),
                execution_mode=exec_mode,
                context={
                    "source": "master_brain",
                    "run_id": self.run_id,
                    "allow_high_risk_auto_approval": allow_high_risk,
                },
                dry_run=False,
                wait_for_results=True,
                step_timeout_seconds=float(
                    str(os.getenv("MASTER_BRAIN_DESKTOP_STEP_TIMEOUT_SEC") or "35")
                ),
                stop_on_failure=_env_bool("MASTER_BRAIN_DESKTOP_STOP_ON_FAILURE", "false"),
                dynamic_risk_controls=_env_bool(
                    "MASTER_BRAIN_DESKTOP_DYNAMIC_RISK_CONTROLS", "false"
                ),
                enforce_quality_gate=_env_bool(
                    "MASTER_BRAIN_DESKTOP_ENFORCE_QUALITY_GATE", "false"
                ),
            )
            response = await run_desktop_autonomy_loop(
                client_id=self.client_id,
                request=req,
                session=session,
            )
        status = str(response.status or "failed")
        return {
            "status": "completed" if status == "completed" else status,
            "summary": str(response.summary or ""),
            "error": str(response.error or ""),
            "data": _safe_jsonable(response.model_dump()),
        }

    async def _execute_n8n(self, step: Dict[str, Any]) -> Dict[str, Any]:
        from tools.n8n_client import send_to_n8n_with_response

        payload = {
            "source": "master_brain",
            "client_id": self.client_id,
            "run_id": self.run_id,
            "step": _safe_jsonable(step),
        }
        result = await send_to_n8n_with_response(payload)
        dispatched = result.get("dispatched", False)
        execution_status = str(result.get("execution_status") or "not_dispatched")
        confirmed = bool(result.get("confirmed"))
        if dispatched:
            if confirmed and execution_status == "success":
                return {
                    "status": "completed",
                    "summary": "n8n execution confirmed. "
                    + f"Paso: {str(step.get('title') or 'sin título')}",
                    "error": "",
                    "data": {
                        "dispatched": True,
                        "pending": False,
                        "confirmed": True,
                        "execution_id": result.get("execution_id"),
                        "execution_status": execution_status,
                        "response_excerpt": result.get("response_excerpt", ""),
                        "webhook_url": (result.get("webhook_url") or "")[:80],
                    },
                }
            if confirmed and execution_status in {"error", "failed", "canceled", "crashed"}:
                return {
                    "status": "failed",
                    "summary": "",
                    "error": f"n8n execution finished with status={execution_status}",
                    "data": {
                        "dispatched": True,
                        "pending": False,
                        "confirmed": True,
                        "execution_id": result.get("execution_id"),
                        "execution_status": execution_status,
                    },
                }
            return {
                "status": "pending",
                "summary": "Dispatched to n8n (execution not confirmed yet). "
                + f"Paso: {str(step.get('title') or 'sin título')}",
                "error": "",
                "data": {
                    "dispatched": True,
                    "pending": True,
                    "confirmed": False,
                    "execution_id": result.get("execution_id"),
                    "execution_status": execution_status,
                    "response_excerpt": result.get("response_excerpt", ""),
                    "webhook_url": (result.get("webhook_url") or "")[:80],
                },
            }
        return {
            "status": "failed",
            "summary": "",
            "error": "n8n unavailable — action not committed",
            "data": {"dispatched": False, "pending": False},
        }

    def _evaluate_and_summarize(
        self,
        *,
        goal: str,
        context: Dict[str, Any],
        strategy: Dict[str, Any],
        results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        completed = [item for item in results if str(item.get("status")) == "completed"]
        failed = [item for item in results if str(item.get("status")) == "failed"]

        status = "completed" if completed and not failed else "failed"

        if status == "completed":
            lead = _first_nonempty(
                *(str(item.get("summary") or "") for item in completed[:2]),
            )
            intro = self._business_style_intro(goal, context)
            if intro:
                summary = intro if not lead else f"{intro} {lead}".strip()
            else:
                summary = lead or "Objetivo ejecutado correctamente."
        else:
            summary = _first_nonempty(
                *(str(item.get("error") or "") for item in failed[:2]),
                "No pude completar el objetivo.",
            )

        merged_data: Dict[str, Any] = {}
        for item in completed:
            payload = item.get("data")
            if isinstance(payload, dict):
                merged_data.update(payload)

        return {
            "status": status,
            "summary": summary[:420],
            "data": merged_data,
            "completed_steps": len(completed),
            "failed_steps": len(failed),
            "strategy_objectives": _safe_jsonable(strategy.get("objectives") or []),
        }

    def _strategy_summary(
        self,
        goal: str,
        context: Dict[str, Any],
        strategy: Dict[str, Any],
    ) -> str:
        intro = self._business_style_intro(goal, context)
        mission_steps = [str(item.get("title") or "") for item in list(strategy.get("mission_steps") or [])]
        mission_preview = ", ".join([x for x in mission_steps[:2] if x])
        if mission_preview:
            return f"{intro} Plan generado: {mission_preview}."
        return intro

    async def _chat_response(self, goal: str) -> str:
        """Generates a real LLM response for conversational messages instead of static text."""
        text = str(goal or "").strip()
        if not text:
            return "Entendido."
        try:
            from brain.core import CognitiveEngine
            engine = CognitiveEngine.get_instance()
            result = await engine.generate_response(
                client_id=self.client_id,
                session_id=f"chat:{self.client_id}",
                message=text,
            )
            reply = str(result.get("content") or result.get("response") or result.get("text") or "").strip()
            return reply if reply else "Entendido."
        except Exception as exc:
            logger.warning("[MASTER_BRAIN] _chat_response fallback: %s", exc)
            if "gracias" in text.lower():
                return "Con gusto."
            return "Entendido."

    def _business_style_intro(self, goal: str, context: Dict[str, Any]) -> str:
        intent = str(context.get("intent") or "")
        if intent not in {"business", "mixed"}:
            return ""
        industry = str(self._infer_industry(goal, context) or "general")
        industry_label = {
            "restaurants": "restaurantes",
            "clinics": "clínicas",
            "real_estate": "inmobiliarias",
            "gyms": "gimnasios",
            "ecommerce": "ecommerce",
            "general": "negocios con procesos repetitivos",
        }.get(industry, "negocios con procesos repetitivos")
        return (
            "Analizando tu negocio detecto una oportunidad clara de automatización. "
            f"El mercado objetivo inicial son {industry_label}."
        )

    async def _learn(
        self,
        goal: str,
        context: Dict[str, Any],
        results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self._enable_learning:
            return {"enabled": False, "reason": "MASTER_BRAIN_ENABLE_LEARNING=false"}

        org_id = self._parse_client_uuid(self.client_id)
        if org_id is None:
            return {"enabled": False, "reason": "invalid_client_id"}

        try:
            async with self._session_factory() as session:
                optimization = await self._continuous_optimizer(
                    session,
                    organization_id=org_id,
                    lookback_days=max(
                        7, int(str(os.getenv("MASTER_BRAIN_LEARNING_WINDOW_DAYS") or "14"))
                    ),
                )
                refreshed = await self._global_refresh(
                    session,
                    min_samples=max(
                        1, int(str(os.getenv("MASTER_BRAIN_LEARNING_MIN_SAMPLES") or "3"))
                    ),
                )
                for item in results:
                    executor = str(item.get("executor") or "").strip().lower()
                    tool = _EXECUTOR_TO_TOOL.get(executor, "")
                    if not tool:
                        continue
                    await self._tool_outcome_recorder(
                        session,
                        organization_id=org_id,  # UUID, not raw string
                        tool=tool,
                        success=str(item.get("status")) == "completed",
                        duration_ms=int(item.get("duration_ms") or 0) or None,
                        extra={"source": "master_brain", "goal": goal[:180]},
                    )
                payload = _safe_jsonable(optimization)
                if not isinstance(payload, dict):
                    payload = {"optimization": payload}
                payload["refreshed_patterns"] = len(list(refreshed or []))
                payload["goal"] = goal[:180]
                payload["intent"] = str(context.get("intent") or "")
                return payload
        except Exception as exc:
            err_str = str(exc)
            # Schema mismatch: objective_runs (or similar) missing columns; avoid noisy WARNING.
            if "objective_runs" in err_str and "does not exist" in err_str:
                logger.debug("[MASTER_BRAIN] learning skipped (schema mismatch objective_runs): %s", err_str[:200])
                return {
                    "enabled": False,
                    "reason": "schema_mismatch_objective_runs",
                    "objective_runs_analyzed": 0,
                    "goal": goal[:180],
                }
            logger.warning("[MASTER_BRAIN] learning failed: %s", exc)
            return {"enabled": False, "error": err_str}

    def _risk_level_for_action(self, *, action_type: str, action: str, args: Dict[str, Any]) -> str:
        probe = " ".join(
            [
                str(action_type or ""),
                str(action or ""),
                json.dumps(args, ensure_ascii=True, sort_keys=True),
            ]
        ).lower()
        if any(token in probe for token in _DANGEROUS_KEYWORDS):
            return "critical"
        if "payment" in probe or "billing" in probe:
            return "high"
        if action_type in {"desktop", "web"}:
            return "medium"
        return "low"

    def _parse_client_uuid(self, raw: str) -> Optional[UUID]:
        try:
            return UUID(str(raw))
        except Exception:
            return None

    def _build_response(
        self,
        *,
        status: str,
        summary: str,
        data: Dict[str, Any],
        iterations: int,
        steps: List[Dict[str, Any]],
        needs_confirmation: bool,
        confirmation_reason: Optional[str],
        runtime: str = "master_brain",
    ) -> Dict[str, Any]:
        return {
            "status": str(status or "failed").strip().lower(),
            "summary": str(summary or "").strip(),
            "data": _safe_jsonable(data),
            "runtime": str(runtime or "master_brain").strip() or "master_brain",
            "iterations": max(0, int(iterations)),
            "steps": _safe_jsonable(steps),
            "needs_confirmation": bool(needs_confirmation),
            "confirmation_reason": (
                str(confirmation_reason).strip() if confirmation_reason else None
            ),
            "run_id": self.run_id,
        }

    def _derive_runtime(self, results: List[Dict[str, Any]]) -> str:
        executors = {
            str(item.get("executor") or "").strip().lower()
            for item in results
            if isinstance(item, dict)
        }
        executors.discard("")
        if not executors:
            return "master_brain"
        if executors == {"web_agent"}:
            return "web_agent_loop"
        if executors == {"autonomy_v2"}:
            return "autonomy_v2"
        if executors == {"n8n"}:
            return "n8n"
        if executors in ({"desktop_loop"}, {"autonomous_agent"}):
            return "desktop_autonomy"
        return "master_brain"
