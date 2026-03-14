from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from brain.runtime_hooks import HOOK_AFTER_RUN, HOOK_BEFORE_PROMPT, runtime_hooks
from brain.sandbox_executor import SandboxExecutor
from schemas.action_contract import ActionContract
from schemas.sandbox import SandboxResult, SandboxStep

# Context compression: when steps exceed this count, summarize older ones
_COMPRESS_AFTER_STEPS = int(os.getenv("SANDBOX_COMPRESS_AFTER", "6"))

logger = logging.getLogger("kan_core.llm_sandbox")

_MAX_ITERATIONS = int(os.getenv("SANDBOX_MAX_ITERATIONS", "10"))
_AUTO_COMMIT = os.getenv("SANDBOX_AUTO_COMMIT", "true").lower() in {"1", "true", "yes"}
_COMMIT_THRESHOLD = float(os.getenv("SANDBOX_COMMIT_THRESHOLD", "0.80"))

_REACT_SYSTEM = """\
Eres un agente autónomo con acceso a herramientas. Tu objetivo es completar la tarea \
usando las herramientas disponibles, observando los resultados y adaptándote.

{memory_hint}

INTEGRACIONES YA CONFIGURADAS (JSON lines):
{integrations}

HERRAMIENTAS DISPONIBLES (action_type):

1. integration — Usar una integración ya configurada (va a n8n):
   {{"contract_version":"1.0","action_type":"integration","action":"get_orders",
     "integration":"shopify","arguments":{{"limit":10}},"rationale":"..."}}

2. discover — Descubrir una API desconocida (crawl + OpenAPI, nativo, sin n8n):
   · desde URL:    {{"action_type":"discover","action":"from_url","integration":"discovery_engine",
                   "arguments":{{"url":"https://api.example.com"}},"rationale":"..."}}
   · desde spec:   {{"action_type":"discover","action":"from_openapi","integration":"discovery_engine",
                   "arguments":{{"openapi_spec":{{...}}}},"rationale":"..."}}
   → OBSERVATION contendrá los endpoints. Úsalos con action_type="http" en el siguiente paso.

3. http — Llamar directamente cualquier endpoint HTTP descubierto:
   {{"contract_version":"1.0","action_type":"http","action":"GET /v1/orders",
     "integration":"erp_api","http_method":"GET",
     "arguments":{{"url":"https://erp.example.com/v1/orders",
                  "params":{{"status":"pending"}},
                  "auth_integration":"erp_api"}},"rationale":"..."}}
   · auth_integration: nombre de integración en DB con las credenciales
   · DESTRUCTIVE methods (POST/PUT/DELETE) solo se ejecutan en commit, no en dry-run.

4. code — Ejecutar Python para transformar datos entre pasos (sandbox seguro):
   {{"contract_version":"1.0","action_type":"code","action":"filter_results",
     "integration":"none",
     "arguments":{{"code":"data=[1,2,3]\\nprint([x for x in data if x>1])",
                  "vars":{{"prev_data":"..."}}}},"rationale":"..."}}
   · Solo builtins seguros, sin imports, sin red. stdout = observation.

5. wait — Pausar N segundos (polling, async):
   {{"contract_version":"1.0","action_type":"wait","action":"pause",
     "integration":"none","arguments":{{"seconds":10,"reason":"esperando webhook"}},"rationale":"..."}}

6. desktop — Enviar al agente de escritorio (clicks, screenshots):
   {{"contract_version":"1.0","action_type":"desktop","action":"take_screenshot",
     "integration":"desktop","arguments":{{}},"rationale":"..."}}

7. register — Guardar API descubierta en DB para uso futuro:
   {{"contract_version":"1.0","action_type":"register","action":"save",
     "integration":"none",
     "arguments":{{"name":"mi_erp","base_url":"https://api.erp.com",
                  "auth_type":"bearer","secret":"sk-xxxx",
                  "endpoints":[...]}},"rationale":"guardo para no redescubrir"}}

8. workflow — Generar y desplegar workflow en n8n desde endpoints:
   {{"contract_version":"1.0","action_type":"workflow","action":"generate_and_deploy",
     "integration":"none",
     "arguments":{{"name":"erp_sync","base_url":"https://api.erp.com",
                  "endpoints":[{{"method":"GET","path":"/orders","summary":"Get orders"}}],
                  "trigger":"webhook","auth_integration":"mi_erp"}},"rationale":"..."}}
   → dry-run: muestra JSON del workflow sin desplegar
   → commit: despliega en n8n y devuelve workflow_id

FLUJO RECOMENDADO para APIs desconocidas:
  Paso 0: revisar INTEGRACIONES YA CONFIGURADAS — si ya existe, usar action_type="integration" directo.
  Paso 1: discover (from_url)    → obtener endpoints disponibles
  Paso 2: register               → guardar API en DB con credenciales
  Paso 3: http (GET /endpoint)   → verificar auth y obtener datos
  Paso 4: code                   → transformar / filtrar datos
  Paso 5: workflow               → generar automatización n8n permanente
  Paso 6: FINAL_ANSWER           → responder al usuario con resumen

REGLAS:
1. Responde SIEMPRE con UNO de estos dos formatos:

   Opción A:  THOUGHT: <razonamiento>
              ACTION: <JSON ActionContract>

   Opción B:  THOUGHT: <razonamiento>
              FINAL_ANSWER: <respuesta completa para el usuario>

2. El JSON debe incluir: contract_version, action_type, action, integration, arguments, rationale.
3. Nunca inventes datos — usa solo lo observado.
4. Si falla, cambia de estrategia: distinto action_type, distinto endpoint, distinto argumento.
   Si la misma combinación (action_type + action + url/integration principal) falla 3 veces
   sin cambio de enfoque → usa FINAL_ANSWER describiendo el bloqueo exacto y qué intentaste.
5. Responde en el mismo idioma que el usuario.
6. En el PRIMER paso: el THOUGHT debe incluir un plan de 2-4 pasos numerados antes de la
   primera ACTION. Actualiza el plan si el objetivo cambia durante la ejecución.
7. Usa FINAL_ANSWER en cuanto el objetivo esté genuinamente completo — no agregues pasos vacíos
   para "completar". Pero si hay trabajo real pendiente, continúa: no des FINAL_ANSWER prematura
   por comodidad o incertidumbre. Tienes hasta {max_iterations} iteraciones.

OBJETIVO: {goal}

{history}"""


def _format_integrations(integrations: Optional[List[Any]]) -> str:
    if not integrations:
        return "(ninguna integración activa)"
    lines = []
    for integ in integrations:
        try:
            lines.append(
                json.dumps(
                    {
                        "integration": str(getattr(integ, "name", "")),
                        "base_url": getattr(integ, "base_url", None),
                        "auth_type": getattr(integ, "auth_type", None),
                    },
                    ensure_ascii=True,
                )
            )
        except Exception:
            continue
    return "\n".join(lines) or "(ninguna integración activa)"


async def _compress_history(
    steps: List[SandboxStep],
    engine: Any,
    *,
    keep_last: int = 3,
) -> str:
    """When history is long, summarize older steps with the LLM and keep recent ones verbatim.

    Returns a single formatted history string (compressed + recent).
    """
    if len(steps) <= keep_last:
        return _format_history(steps)

    old_steps = steps[:-keep_last]
    recent_steps = steps[-keep_last:]

    # Build summary prompt
    old_text = _format_history(old_steps)
    summary_prompt = (
        "Resume en 3-5 líneas las acciones y observaciones anteriores del agente. "
        "Menciona qué APIs/datos se encontraron, qué funcionó y qué falló. "
        "Sé conciso.\n\n" + old_text
    )
    try:
        model_name = engine._resolve_model_name(None)
        body = engine._build_request(
            system_prompt="Eres un asistente que resume transcripciones de forma concisa.",
            history=[],
            message=summary_prompt,
            model_name=model_name,
            temperature=0.1,
        )
        resp = await engine._call_model(model_name, body)
        summary_text = engine._extract_text(resp).strip()
    except Exception:
        # Fallback: just list action names
        summary_text = "Pasos anteriores: " + "; ".join(
            str((s.action or {}).get("action", "?")) for s in old_steps
        )

    compressed_header = f"[RESUMEN DE PASOS 1-{len(old_steps)}]\n{summary_text}"
    recent_text = _format_history(recent_steps)
    return compressed_header + "\n\n" + recent_text if recent_text else compressed_header


def _extract_discovered_endpoints(step: Any) -> Optional[str]:
    """If a step was a discover action, return a structured endpoint summary for the LLM."""
    action = step.action or {}
    if str(action.get("action_type") or "").lower() != "discover":
        return None
    raw = step.dry_run_result or {}
    # from_openapi result
    endpoints = raw.get("endpoints") or []
    if endpoints:
        lines = [
            f"  {e.get('method','GET').upper()} {e.get('path','')} — {e.get('summary') or e.get('operation_id','')}"
            for e in endpoints[:20]
        ]
        base_url = raw.get("base_url", "")
        return f"DISCOVERED_API base_url={base_url}:\n" + "\n".join(lines)
    # from_url result (list of discovered_apis)
    discovered = raw.get("discovered_apis") or []
    if discovered:
        lines = []
        for api in discovered[:3]:
            base = api.get("base_url") or api.get("url") or ""
            eps = api.get("endpoints") or []
            for e in eps[:10]:
                lines.append(
                    f"  {e.get('method','').upper()} {base}{e.get('path','')} — {e.get('summary','')}"
                )
        if lines:
            return "DISCOVERED_API:\n" + "\n".join(lines)
    return None


def _format_history(steps: List[SandboxStep]) -> str:
    if not steps:
        return ""
    parts = []
    for s in steps:
        parts.append(f"[Paso {s.step_index + 1}]")
        parts.append(f"THOUGHT: {s.thought}")
        if s.action:
            parts.append(f"ACTION: {json.dumps(s.action, ensure_ascii=False)}")
        if s.observation:
            parts.append(f"OBSERVATION: {s.observation}")
        # Rich structured endpoint list for discover steps
        discovered_summary = _extract_discovered_endpoints(s)
        if discovered_summary:
            parts.append(discovered_summary)
        if s.error:
            parts.append(f"ERROR: {s.error}")
    return "\n".join(parts)


def _primary_action_target(action: Dict[str, Any]) -> str:
    """Return the main target of an action for repeated-failure bucketing."""
    arguments = action.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    url = str(arguments.get("url") or "").strip()
    if url:
        return url
    integration = str(action.get("integration") or "").strip()
    if integration:
        return integration
    return "none"


def _failure_signature(action: Dict[str, Any]) -> str:
    """Stable-ish failure signature for loop-guard purposes."""
    action_type = str(action.get("action_type") or "").strip().lower()
    action_name = str(action.get("action") or "").strip().lower()
    target = _primary_action_target(action).lower()
    return f"{action_type}|{action_name}|{target}"


def _parse_llm_output(
    text: str,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Parse a single LLM iteration response.

    Returns:
        (thought, final_answer_or_None, action_dict_or_None)
    """
    text = text.strip()

    # Extract THOUGHT section (everything up to ACTION: or FINAL_ANSWER:)
    thought = ""
    thought_match = re.search(
        r"THOUGHT:\s*(.+?)(?=\n(?:ACTION|FINAL_ANSWER):|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if thought_match:
        thought = thought_match.group(1).strip()
    else:
        # Fallback: first non-empty line as thought
        for line in text.splitlines():
            line = line.strip()
            if line and not line.upper().startswith(("ACTION:", "FINAL_ANSWER:")):
                thought = line
                break
        if not thought:
            thought = text[:200]

    # FINAL_ANSWER takes priority over ACTION
    final_match = re.search(
        r"FINAL_ANSWER:\s*(.+)$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if final_match:
        return thought, final_match.group(1).strip(), None

    # Look for ACTION: { ... }
    action: Optional[Dict[str, Any]] = None
    action_match = re.search(
        r"ACTION:\s*(\{.*\})",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if action_match:
        try:
            data = json.loads(action_match.group(1).strip())
            if isinstance(data, dict):
                action = data
        except json.JSONDecodeError:
            pass

    return thought, None, action


class LLMSandbox:
    """ReAct loop orchestrator: Think → Act (dry-run) → Observe → repeat.

    The LLM calls tools iteratively, each time seeing the observation from the
    previous step, until it emits FINAL_ANSWER or max_iterations is reached.
    Actions are always executed in dry-run mode during the loop; real production
    commits happen after the loop (auto or manual).
    """

    def __init__(self, *, engine: Any) -> None:
        # engine is a CognitiveEngine instance, passed to avoid circular imports
        self._engine = engine

    async def run(
        self,
        *,
        goal: str,
        client_id: str,
        session_id: str,
        integrations: Optional[List[Any]] = None,
        max_iterations: int = _MAX_ITERATIONS,
        auto_commit: bool = _AUTO_COMMIT,
        dry_run_only: bool = False,
    ) -> SandboxResult:
        run_id = str(uuid.uuid4())
        executor = SandboxExecutor(client_id=client_id, session_id=session_id)
        steps: List[SandboxStep] = []
        total_tokens = 0
        stopped_reason = "max_iterations"
        integ_text = _format_integrations(integrations)
        consecutive_failure_signature: Optional[str] = None
        consecutive_failure_count = 0

        # Load task memory — inject past successful trajectories into first prompt
        memory_hint = ""
        try:
            from brain.task_memory import format_memory_hint, get_similar_runs
            past_runs = await get_similar_runs(goal, client_id=client_id, limit=2)
            memory_hint = format_memory_hint(past_runs)
        except Exception:
            pass

        # Load specialist memory — agent stats (success rate, known failures)
        try:
            from brain.specialist_memory import specialist_memory
            sm_data = specialist_memory.summary(client_id=client_id, specialist="sandbox")
            if sm_data.get("count", 0) > 0:
                sm_lines = [
                    f"HISTORIAL DEL AGENTE: {sm_data['count']} ejecuciones previas, "
                    f"tasa_éxito={sm_data['success_rate']:.0%}, "
                    f"confianza_promedio={sm_data['avg_confidence']:.2f}",
                ]
                if sm_data.get("recent_failures"):
                    sm_lines.append(
                        "Fallos recientes: " + "; ".join(sm_data["recent_failures"][-3:])
                    )
                specialist_hint = "\n".join(sm_lines)
                memory_hint = specialist_hint + ("\n" + memory_hint if memory_hint else "")
        except Exception:
            pass

        if (
            os.getenv("ENABLE_ANTHROPIC_AGENTIC_RUNTIME", "false").lower() in {"1", "true", "yes"}
            and str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        ):
            try:
                from brain.anthropic_agent_runtime import AnthropicAgentRuntime

                system_prompt = _REACT_SYSTEM.format(
                    memory_hint=memory_hint,
                    integrations=integ_text,
                    goal=goal,
                    history="",
                    max_iterations=max_iterations,
                )
                runtime = AnthropicAgentRuntime(engine=self._engine)
                result = await runtime.run_tool_loop(
                    system_prompt=system_prompt,
                    user_message="Resuelve la tarea usando tools cuando sean necesarias.",
                    domain="sandbox",
                    max_rounds=max_iterations,
                )
                final_answer = str(result.get("final_answer") or "").strip()
                if final_answer:
                    return SandboxResult(
                        run_id=run_id,
                        goal=goal,
                        steps=[
                            SandboxStep(
                                step_index=0,
                                thought="anthropic_interleaved",
                                observation=final_answer,
                                is_final=True,
                            )
                        ],
                        final_answer=final_answer,
                        committed=False,
                        pending_commit=False,
                        iterations=int(result.get("tool_rounds") or 1),
                        total_tokens=int((result.get("usage") or {}).get("total_tokens") or 0),
                        stopped_reason="final_answer",
                        client_id=client_id,
                        session_id=session_id,
                    )
            except Exception:
                pass

        for i in range(max(1, max_iterations)):
            # Context compression: summarize old steps when history grows large
            if len(steps) > _COMPRESS_AFTER_STEPS:
                history_text = await _compress_history(
                    steps, self._engine, keep_last=3
                )
            else:
                history_text = _format_history(steps)

            # Only show memory hint on first iteration (avoid bloating context)
            active_memory_hint = memory_hint if i == 0 else ""

            system_prompt = _REACT_SYSTEM.format(
                memory_hint=active_memory_hint,
                integrations=integ_text,
                goal=goal,
                history=history_text,
                max_iterations=max_iterations,
            )

            hook_payload: Dict[str, Any] = {
                "system_prompt": system_prompt,
                "iteration": i,
                "goal": goal,
                "run_id": run_id,
            }
            hook_payload = await runtime_hooks.run(HOOK_BEFORE_PROMPT, hook_payload)
            system_prompt = str(hook_payload.get("system_prompt", system_prompt))

            model_name = self._engine._resolve_model_name(None)
            request_body = self._engine._build_request(
                system_prompt=system_prompt,
                history=[],
                message="Procede con el siguiente paso.",
                model_name=model_name,
                temperature=0.2,
            )

            try:
                response_json = await self._engine._call_model(model_name, request_body)
            except Exception as exc:
                logger.exception("LLMSandbox iteration %d failed: %s", i, exc)
                step = SandboxStep(
                    step_index=i,
                    thought="[error interno del modelo]",
                    error=str(exc),
                    is_final=True,
                )
                steps.append(step)
                stopped_reason = "error"
                break

            usage = self._engine._extract_usage(response_json)
            if usage:
                total_tokens += usage.get("total_tokens", 0)

            llm_text = self._engine._extract_text(response_json)
            thought, final_answer, action_dict = _parse_llm_output(llm_text)

            if final_answer is not None:
                steps.append(
                    SandboxStep(step_index=i, thought=thought, is_final=True)
                )
                stopped_reason = "final_answer"
                result = SandboxResult(
                    run_id=run_id,
                    goal=goal,
                    steps=steps,
                    final_answer=final_answer,
                    committed=False,
                    pending_commit=False,
                    iterations=i + 1,
                    total_tokens=total_tokens,
                    stopped_reason=stopped_reason,
                    client_id=client_id,
                    session_id=session_id,
                )
                if auto_commit and not dry_run_only:
                    result = await self._maybe_commit(result, executor, client_id=client_id)
                await runtime_hooks.run(
                    HOOK_AFTER_RUN, {"run_id": run_id, "result": result.model_dump()}
                )
                # Record successful trajectory in task memory for future runs
                try:
                    from brain.task_memory import record_run
                    await record_run(result)
                except Exception:
                    pass
                # Record in specialist memory for agent stats
                try:
                    from brain.specialist_memory import specialist_memory
                    avg_conf = (
                        sum(s.observation_confidence for s in result.steps)
                        / max(1, len(result.steps))
                    )
                    specialist_memory.record(
                        client_id=client_id,
                        specialist="sandbox",
                        goal=goal,
                        status="ok",
                        confidence=avg_conf,
                        metadata={"iterations": result.iterations, "run_id": run_id},
                    )
                except Exception:
                    pass
                return result

            # Execute action (always dry-run during the loop)
            observation_text = "(no se pudo parsear la acción)"
            observation_confidence = 0.5
            dry_run_result: Optional[Dict[str, Any]] = None

            if action_dict:
                try:
                    action_model = ActionContract.model_validate(action_dict)
                    obs = await executor.execute(
                        action_model,
                        dry_run=True,
                        action_id=f"{run_id}-step{i}",
                    )
                    observation_text = obs.result_preview
                    if obs.error:
                        observation_text = f"[error] {obs.error}"
                    observation_confidence = obs.confidence
                    dry_run_result = obs.raw_payload
                except Exception as exc:
                    observation_text = f"[error al validar/ejecutar acción] {exc}"
                    observation_confidence = 0.0

            steps.append(
                SandboxStep(
                    step_index=i,
                    thought=thought,
                    action=action_dict,
                    observation=observation_text,
                    is_final=False,
                    dry_run_result=dry_run_result,
                    observation_confidence=observation_confidence,
                )
            )

            if action_dict:
                failed = observation_confidence <= 0.0 or observation_text.startswith("[error")
                if failed:
                    signature = _failure_signature(action_dict)
                    if signature == consecutive_failure_signature:
                        consecutive_failure_count += 1
                    else:
                        consecutive_failure_signature = signature
                        consecutive_failure_count = 1
                    if consecutive_failure_count >= 3:
                        stopped_reason = "repeated_failures"
                        final_answer = (
                            "Se detecto un bloqueo: la misma accion fallo 3 veces seguidas "
                            f"({action_dict.get('action_type')} / {action_dict.get('action')}). "
                            "Detengo mas reintentos para evitar un loop."
                        )
                        steps.append(
                            SandboxStep(
                                step_index=i + 1,
                                thought="[bloqueo por fallos repetidos]",
                                is_final=True,
                            )
                        )
                        result = SandboxResult(
                            run_id=run_id,
                            goal=goal,
                            steps=steps,
                            final_answer=final_answer,
                            committed=False,
                            pending_commit=False,
                            iterations=i + 1,
                            total_tokens=total_tokens,
                            stopped_reason=stopped_reason,
                            client_id=client_id,
                            session_id=session_id,
                        )
                        await runtime_hooks.run(
                            HOOK_AFTER_RUN, {"run_id": run_id, "result": result.model_dump()}
                        )
                        return result
                else:
                    consecutive_failure_signature = None
                    consecutive_failure_count = 0

        # Reached max_iterations without FINAL_ANSWER
        result = SandboxResult(
            run_id=run_id,
            goal=goal,
            steps=steps,
            final_answer=None,
            committed=False,
            pending_commit=True,
            iterations=max_iterations,
            total_tokens=total_tokens,
            stopped_reason=stopped_reason,
            client_id=client_id,
            session_id=session_id,
        )
        await runtime_hooks.run(
            HOOK_AFTER_RUN, {"run_id": run_id, "result": result.model_dump()}
        )
        return result

    async def _maybe_commit(
        self,
        result: SandboxResult,
        executor: SandboxExecutor,
        *,
        client_id: str,
    ) -> SandboxResult:
        """Auto-commit if all action steps meet the adaptive confidence threshold."""
        action_steps = [s for s in result.steps if s.action and not s.is_final]
        if not action_steps:
            return result

        threshold = _COMMIT_THRESHOLD
        try:
            import uuid as _uuid
            from database import AsyncSessionLocal
            from brain.continuous_improvement import get_confidence_threshold

            try:
                org_id = _uuid.UUID(client_id)
                async with AsyncSessionLocal() as db_session:
                    threshold = await get_confidence_threshold(
                        db_session, organization_id=org_id
                    )
            except Exception:
                pass
        except Exception:
            pass

        if all(s.observation_confidence >= threshold for s in action_steps):
            committed_count = await executor.commit_all(action_steps)
            data = result.model_dump()
            data["committed"] = committed_count > 0
            data["pending_commit"] = committed_count == 0
            return SandboxResult(**data)
        else:
            data = result.model_dump()
            data["pending_commit"] = True
            return SandboxResult(**data)
