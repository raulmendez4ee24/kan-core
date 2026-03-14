import asyncio
import logging
import os
import json
import traceback
import time
from uuid import UUID
from typing import Any, Dict, Optional

from brain.action_policy import requires_human_approval
from brain.core import CognitiveEngine
import brain.goal_tracker as goal_tracker
from database import AsyncSessionLocal
from models import Client
from observability import capture_exception
from schemas.action_contract import ActionContract
from sqlalchemy import select
from tools.n8n_client import send_to_n8n_with_response
from tools.telegram_client import send_telegram_message
from tools.slack_client import send_slack_message
from tools.discord_client import send_discord_message
from tools.teams_client import send_teams_message
from tools.ycloud_client import send_ycloud_whatsapp_text_message
from brain.jarvis_ui_state import publish_state
from brain.specialist_memory import specialist_memory
from brain.voice_engine import speak_text

logger = logging.getLogger("kan_core.dispatcher")
_RUNTIME_TELEGRAM_CHAT_MAP: Dict[str, str] = {}
# Guarda el último resultado de tarea por chat_id para responder "como vas?"
_LAST_TASK: Dict[str, Dict] = {}
# Runs por run_id para /status, /runs, /stop (timeout → running, luego notificación final)
_TELEGRAM_RUNS: Dict[str, Dict[str, Any]] = {}
_TELEGRAM_RUNS_LOCK: Optional[asyncio.Lock] = None
_TELEGRAM_AGENT_BRIDGES: Dict[str, "_TelegramAgentBridge"] = {}
_TELEGRAM_AGENT_BRIDGE_LOCK: Optional[asyncio.Lock] = None

# Timeouts por tipo (segundos): chat no pasa por aquí; agent runs usan estos.
TELEGRAM_AGENT_TIMEOUT_DEFAULT = int(os.getenv("TELEGRAM_AGENT_TIMEOUT_SECONDS", "120"))


def _extract_execution_fields(payload: Dict[str, Any]) -> tuple[str, str, str, list, str]:
    return _extract_execution_fields_from_payload(payload)


class _TelegramAgentBridge:
    def __init__(self, *, client_id: str, execution_mode: str, max_iterations: int) -> None:
        self.client_id = str(client_id or "").strip()
        self.execution_mode = str(execution_mode or "autonomous").strip() or "autonomous"
        self.max_iterations = max(1, int(max_iterations))
        self._running_task: Optional[asyncio.Task] = None

    async def run_goal(self, goal: str) -> dict:
        from brain.agent_bridge import AgentBridge
        goal_text = str(goal or "").strip()
        bridge = AgentBridge(client_id=self.client_id, max_iterations=self.max_iterations)
        # Detach into an independent task so Telegram webhook retries / connection drops
        # don't kill execution mid-flight. Shield prevents external cancellation from
        # propagating into the bridge while still letting our timeout work.
        task = asyncio.ensure_future(bridge.run(goal_text))
        self._running_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Outer context was cancelled (e.g. Telegram retry received before this one
            # finished). The inner bridge task is still running — wait briefly for it.
            logger.warning(
                "[_TelegramAgentBridge] run_goal cancelled externally goal=%r — "
                "waiting up to 10s for bridge to finish",
                goal_text[:60],
            )
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            except Exception:
                pass
            # Could not recover — mark failed so the user gets a real reply.
            return {
                "status": "failed",
                "goal": goal_text,
                "summary": "La tarea fue interrumpida. Intenta de nuevo.",
                "data": {},
                "iterations": 0,
                "steps": [],
                "run_id": "",
            }
        except Exception as exc:
            task.cancel()
            logger.exception("[_TelegramAgentBridge] bridge.run raised: %s", exc)
            return {
                "status": "failed",
                "goal": goal_text,
                "summary": str(exc) or "Error en ejecución.",
                "data": {},
                "iterations": 0,
                "steps": [],
                "run_id": "",
            }
        finally:
            self._running_task = None


async def _get_telegram_agent_bridge(client_id: str) -> _TelegramAgentBridge:
    global _TELEGRAM_AGENT_BRIDGE_LOCK
    if _TELEGRAM_AGENT_BRIDGE_LOCK is None:
        _TELEGRAM_AGENT_BRIDGE_LOCK = asyncio.Lock()
    client_key = str(client_id or "").strip()
    async with _TELEGRAM_AGENT_BRIDGE_LOCK:
        existing = _TELEGRAM_AGENT_BRIDGES.get(client_key)
        if existing is not None:
            return existing
        bridge = _TelegramAgentBridge(
            client_id=client_key,
            execution_mode=str(os.getenv("TELEGRAM_AGENT_EXECUTION_MODE") or "autonomous").strip() or "autonomous",
            max_iterations=int(str(os.getenv("TELEGRAM_AGENT_MAX_STEPS") or "8")),
        )
        _TELEGRAM_AGENT_BRIDGES[client_key] = bridge
        return bridge


def _load_meta_page_client_map() -> Dict[str, str]:
    return _load_map("META_PAGE_CLIENT_MAP_JSON")


def _load_ycloud_channel_client_map() -> Dict[str, str]:
    return _load_map("YCLOUD_CHANNEL_CLIENT_MAP_JSON")


def _load_map(env_key: str) -> Dict[str, str]:
    raw = os.getenv(env_key, "{}")
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in data.items():
        k = str(key).strip()
        v = str(value).strip()
        if k and v:
            out[k] = v
    return out


def _resolve_client_id(mapping: Dict[str, str], key: str) -> Optional[str]:
    token = str(key).strip()
    if token in mapping:
        return mapping[token]
    if "*" in mapping:
        return mapping["*"]
    return None


async def _client_exists(client_id: str) -> bool:
    try:
        org_id = UUID(str(client_id))
    except Exception:
        return False
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Client.id).where(Client.id == org_id).limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row is not None
    except Exception:
        return False


def _extract_telegram_message_text(message: Dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    if text:
        return text
    caption = str(message.get("caption") or "").strip()
    if message.get("voice"):
        voice_id = str((message.get("voice") or {}).get("file_id") or "").strip()
        return caption or f"[voice note id={voice_id}]"
    if message.get("document"):
        doc = dict(message.get("document") or {})
        file_name = str(doc.get("file_name") or "").strip()
        file_id = str(doc.get("file_id") or "").strip()
        return caption or f"[document name={file_name or 'unknown'} id={file_id}]"
    photos = list(message.get("photo") or [])
    if photos:
        largest = dict(photos[-1] or {})
        file_id = str(largest.get("file_id") or "").strip()
        return caption or f"[photo id={file_id}]"
    if message.get("audio"):
        audio_id = str((message.get("audio") or {}).get("file_id") or "").strip()
        return caption or f"[audio id={audio_id}]"
    if message.get("video"):
        video_id = str((message.get("video") or {}).get("file_id") or "").strip()
        return caption or f"[video id={video_id}]"
    return ""


def _resolve_telegram_client_id(chat_id: str) -> Optional[str]:
    chat_key = str(chat_id).strip()
    if not chat_key:
        return None
    if chat_key in _RUNTIME_TELEGRAM_CHAT_MAP:
        return _RUNTIME_TELEGRAM_CHAT_MAP[chat_key]
    chat_map = _load_map("TELEGRAM_CHAT_CLIENT_MAP_JSON")
    logger.debug(
        "[TELEGRAM_AUTH] chat_id=%r map_keys=%r",
        chat_key,
        list(chat_map.keys())[:10],
    )
    env_mapped = _resolve_client_id(chat_map, chat_key)
    if env_mapped:
        _RUNTIME_TELEGRAM_CHAT_MAP[chat_key] = env_mapped
        return env_mapped
    # Fallback 1: explicit default
    default_client_id = str(os.getenv("TELEGRAM_DEFAULT_CLIENT_ID") or "").strip()
    if default_client_id:
        logger.info("[TELEGRAM_AUTH] using TELEGRAM_DEFAULT_CLIENT_ID for chat_id=%r", chat_key)
        _RUNTIME_TELEGRAM_CHAT_MAP[chat_key] = default_client_id
        return default_client_id
    # Fallback 2: Jarvis client ID (always set for local deployments)
    jarvis_id = str(os.getenv("JARVIS_CLIENT_ID") or os.getenv("KAN_CLIENT_ID") or "").strip()
    if jarvis_id:
        logger.info("[TELEGRAM_AUTH] using JARVIS_CLIENT_ID fallback for chat_id=%r", chat_key)
        _RUNTIME_TELEGRAM_CHAT_MAP[chat_key] = jarvis_id
        return jarvis_id
    logger.warning("[TELEGRAM_AUTH] no client_id found for chat_id=%r map=%r", chat_key, chat_map)
    return None


def _telegram_help_text() -> str:
    return (
        "Comandos:\n"
        "/ping - Verifica que el bot responde\n"
        "/status - Estado de enlace; /status <run_id> - estado de un run\n"
        "/runs - Lista runs recientes\n"
        "/stop <run_id> - Marca run como cancelado\n"
        "/doctor - Autodiagnóstico (OpenAI, n8n, runner, DB)\n"
        "/runner - Estado del Runner de escritorio (conectado, client_id, ws_url)\n"
        "/debug - Diagnóstico completo (webhook, ngrok, API keys)\n"
        "/link <organization_uuid> [codigo] - Vincula este chat\n"
        "/unlink - Quita vinculo temporal de este runtime\n"
        "/run <instruccion> - Ejecuta una instruccion directa\n"
        "/agent <objetivo> - Ejecuta pipeline multi-agent\n"
        "/memory - Resumen de memoria del especialista telegram\n"
        "/help - Muestra esta ayuda\n\n"
        "Flujo por defecto: TELEGRAM_FLOW_MODE=agentbridge_direct\n"
        "Cualquier texto normal se ejecuta en AgentBridge."
    )


def _telegram_agent_mode() -> str:
    mode = str(os.getenv("TELEGRAM_AGENT_MODE") or "off").strip().lower()
    if mode in {"assist", "autonomous"}:
        return mode
    return "off"


def _telegram_flow_mode() -> str:
    mode = str(os.getenv("TELEGRAM_FLOW_MODE") or "agentbridge_direct").strip().lower()
    if mode in {"agentbridge_direct", "legacy"}:
        return mode
    return "agentbridge_direct"


_STATUS_QUERY_PHRASES = frozenset({
    "como vas", "cómo vas", "como vamos", "cómo vamos",
    "qué pasó", "que paso", "que paso?", "qué pasó?",
    "status", "estado", "resultado", "resultados",
    "terminaste", "listo", "ya acabaste", "ya terminaste",
    "y?", "y?", "y bien?", "ok y", "ok, y",
    "que hiciste", "qué hiciste", "que encontraste", "qué encontraste",
    "dame un reporte", "reporte", "resume",
})


def _looks_like_status_query(text: str) -> bool:
    probe = str(text or "").strip().lower().rstrip("?!. ")
    if probe in _STATUS_QUERY_PHRASES:
        return True
    # Frases cortas que contienen palabras clave de estado
    if len(probe) <= 30:
        for phrase in ("como vas", "cómo vas", "qué pasó", "que paso", "terminaste", "resultados", "status"):
            if phrase in probe:
                return True
    return False


# Saludos y frases triviales que NO deben activar el agente
_GREETING_TOKENS = frozenset({
    "hola", "hi", "hello", "hey", "ola", "saludos", "buenas", "buenos dias",
    "buenas tardes", "buenas noches", "que tal", "qué tal", "como estas",
    "cómo estás", "como estan", "cómo están",
})

# Verbos y frases de acción en español/inglés que SÍ activan el agente
_ACTION_VERBS = (
    # originales
    "automatiza", "automation", "workflow", "flujo", "n8n",
    "investiga", "research", "ejecuta", "run", "abre", "desktop", "navega", "autonom",
    # acciones directas en español
    "haz ", "hazme", "hazle", "hazlo", "hazla",
    "busca", "buscar",
    "crea ", "crear", "cree ",
    "envia", "envía", "enviar", "manda", "mandar",
    "revisa", "revisar", "checa", "checar",
    "verifica", "verificar",
    "analiza", "analizar",
    "descarga", "sube", "subir",
    "conecta", "conectar", "integra", "integrar",
    "cierra ", "cerrar",
    "escribe", "escribir",
    "actualiza", "actualizar",
    "agenda", "agendar", "programa ", "programar",
    "compra", "comprar", "vende", "vender",
    "monitorea", "monitorear",
    "genera ", "generame",
    "captura", "screenshot",
    "click ", "llena ", "rellena",
    "necesito que", "quiero que",
    "puedes hacer", "puede hacer",
    "por favor",
    "intenta", "prueba ",
    "toma nota", "registra",
    "abre ", "cierra ", "inicia ", "lanza ",
    "mueve", "copia", "pega", "elimina", "borra",
    "busca en", "ve a", "navega a", "entra a",
    "mira", "dime cuanto", "dime cuantos",
    "trae", "traeme",
)


def _looks_like_agent_goal(text: str) -> bool:
    probe = str(text or "").strip().lower().rstrip("?.! ")
    if not probe:
        return False
    # Mensaje largo → siempre agente
    if len(probe) >= 60:
        return True
    # Saludos simples → chat normal
    if probe in _GREETING_TOKENS:
        return False
    # Contiene verbo de acción → agente
    return any(word in probe for word in _ACTION_VERBS)


_AGENT_FOLLOWUP_PHRASES = frozenset(
    {
        "hazlo",
        "hazla",
        "hazlo ya",
        "procede",
        "ejecutala",
        "ejecútala",
        "ejecutalo",
        "ejecútalo",
        "dale",
        "continua",
        "continúa",
        "ok hazlo",
        "ok procede",
        "si hazlo",
        "sí hazlo",
    }
)


def _autonomous_ack(text: str) -> str:
    probe = str(text or "").strip().lower()
    if "procede" in probe:
        return "Procediendo. Estoy ejecutando el plan."
    if "ejecut" in probe or "haz" in probe:
        return "En marcha. Empiezo a actuar ahora."
    return "Entendido. Lo analizo y ejecuto."


def _should_run_agentbridge_direct(text: str) -> bool:
    """Direct mode should behave like an autonomous operator, not a command parser bot.

    - Execute for actionable goals and follow-up execution commands.
    - Route smalltalk/greetings to conversational engine.
    """
    return _looks_like_agent_goal(text) or _looks_like_agent_followup(text)


def _format_operator_reply(
    *,
    goal: str,
    status: str,
    summary: str,
    error: str,
    steps: list[Dict[str, Any]],
) -> str:
    status_lc = str(status or "").strip().lower()
    detail = str(summary or error or "").strip() or "Sin detalles."
    emoji = {
        "completed": "✅",
        "failed": "❌",
        "blocked": "⛔",
        "dry_run": "🔍",
        "cancelled": "🚫",
    }.get(status_lc, "⚙️")
    return f"{emoji} {detail}"


def _log_bridge_start(channel: str, *, client_id: str, text: str) -> None:
    logger.info(
        "[CORE] Input recibido vía %s -> [BRIDGE] Iniciando ejecución con ID: %s",
        str(channel or "desconocido"),
        client_id or "None",
    )
    logger.info("[%s] Orden recibida: %r", str(channel or "core").upper(), text)


def _looks_like_agent_followup(text: str) -> bool:
    probe = str(text or "").strip().lower()
    if not probe:
        return False
    compact = probe.rstrip("?.! ")
    if compact in _AGENT_FOLLOWUP_PHRASES:
        return True
    if compact.startswith(("hazlo ", "procede ", "ejecuta ", "continua ", "continúa ")):
        return True
    return False


async def _speak_task_completion_report(text: str) -> None:
    try:
        await speak_text(text)
    except Exception as exc:
        logger.warning("voice report failed: %s", exc)


def _schedule_task_completion_voice(
    *,
    status: str,
    goal: str,
    summary: str,
    run_id: str = "",  # noqa: ARG001  kept for call-site compatibility
) -> Optional[asyncio.Task]:
    _ = run_id
    if str(status or "").strip().lower() != "completed":
        return None
    if os.getenv("ENABLE_TASK_VOICE_REPORT", "true").lower() not in {"1", "true", "yes"}:
        return None
    goal_text = str(goal or "").strip()
    summary_text = str(summary or "").strip()
    if "shopify" in goal_text.lower():
        report = "Tarea de Shopify completada."
    elif summary_text:
        report = summary_text[:180]
    elif goal_text:
        report = f"Tarea completada: {goal_text[:140]}"
    else:
        report = "Tarea completada."
    task = asyncio.create_task(_speak_task_completion_report(report))
    task.add_done_callback(lambda t: t.exception() if t.cancelled() is False else None)
    return task


def _extract_execution_fields_from_payload(payload: Dict[str, Any]) -> tuple[str, str, str, list, str]:
    status = str(payload.get("status") or "unknown").strip().lower() or "unknown"
    summary = str(payload.get("summary") or "").strip()
    error = str(payload.get("error") or "").strip()
    run_id = str(payload.get("event_run_id") or payload.get("run_id") or "").strip()
    steps: list = [s for s in list(payload.get("steps") or []) if isinstance(s, dict)]
    raw_results = payload.get("results")
    if isinstance(raw_results, list):
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("result")
            if not isinstance(inner, dict):
                continue
            if not run_id:
                run_id = str(inner.get("event_run_id") or inner.get("run_id") or "").strip()
            execution = inner.get("execution")
            if isinstance(execution, dict):
                if status == "unknown":
                    status = str(execution.get("status") or status).strip().lower() or status
                if not summary:
                    summary = str(execution.get("summary") or "").strip()
                if not error:
                    error = str(execution.get("error") or "").strip()
                if isinstance(execution.get("steps"), list):
                    steps = [s for s in execution.get("steps") if isinstance(s, dict)]
                continue
            if not summary:
                summary = str(inner.get("summary") or "").strip()
            if not error:
                error = str(inner.get("error") or "").strip()
            if isinstance(inner.get("steps"), list):
                steps = [s for s in inner.get("steps") if isinstance(s, dict)]
    return status, summary, error, steps, run_id


async def _run_telegram_agent(
    *,
    client_id: str,
    chat_id: str,
    user_id: str,
    goal: str,
    update: Dict[str, Any],
) -> None:
    import time
    from uuid import uuid4
    goal_text = str(goal or "").strip()
    run_id = uuid4().hex[:8]
    timeout_sec = TELEGRAM_AGENT_TIMEOUT_DEFAULT
    if _TELEGRAM_RUNS_LOCK is None:
        globals()["_TELEGRAM_RUNS_LOCK"] = asyncio.Lock()
    async with _TELEGRAM_RUNS_LOCK:
        _TELEGRAM_RUNS[run_id] = {
            "chat_id": chat_id,
            "client_id": client_id,
            "goal": goal_text,
            "status": "running",
            "started_at": time.time(),
        }

    async def _execute_and_send() -> None:
        bridge_result: Dict[str, Any] = {}
        executor = str(os.getenv("TELEGRAM_AGENT_EXECUTOR") or "agentbridge").strip().lower()
        if executor in {"multi_agent", "handoff"}:
            from brain.multi_agent_runtime import run_multi_agent_handoff
            max_steps = int(str(os.getenv("TELEGRAM_AGENT_MAX_STEPS") or "8"))
            dry_run = os.getenv("TELEGRAM_AGENT_DRY_RUN", "false").lower() in {"1", "true", "yes"}
            context = {"channel": "telegram", "chat_id": chat_id, "user_id": user_id, "dry_run": dry_run, "raw_update": update}
            async with AsyncSessionLocal() as session:
                result = await run_multi_agent_handoff(client_id=client_id, goal=goal_text, context=context, max_steps=max_steps, session=session)
            bridge_result = dict(result or {})
        else:
            logger.info("[BRIDGE] Intentando ejecución con ID: %s", client_id or "None")
            try:
                await send_to_n8n_with_response({"source": "telegram_agentbridge_start", "client_id": client_id, "chat_id": chat_id, "user_id": user_id, "goal": goal_text})
            except Exception as exc:
                capture_exception(exc, {"source": "telegram_agentbridge_start", "chat_id": chat_id})
            bridge = await _get_telegram_agent_bridge(client_id)
            try:
                bridge_result = await bridge.run_goal(goal_text)
            except ValueError as exc:
                if "Client not found" in str(exc):
                    logger.warning("[TELEGRAM_AGENT] Client not found for chat_id=%s", chat_id)
                    bridge_result = {
                        "status": "failed", "goal": goal_text,
                        "summary": "No hay una organización válida para este chat. Usa /link <organization_uuid> o crea el cliente en la base de datos (JARVIS_CLIENT_ID).",
                        "error": "", "steps": [], "run_id": run_id,
                    }
                else:
                    logger.exception("[TELEGRAM_AGENT] bridge.run_goal ValueError for chat_id=%s: %s", chat_id, exc)
                    bridge_result = {"status": "failed", "goal": goal_text, "summary": "No pude completar la tarea. Intenta de nuevo.", "error": str(exc), "steps": [], "run_id": ""}
            except Exception as exc:
                logger.exception("[TELEGRAM_AGENT] bridge.run_goal failed for chat_id=%s: %s", chat_id, exc)
                bridge_result = {"status": "failed", "goal": goal_text, "summary": "No pude completar la tarea. Intenta de nuevo.", "error": str(exc), "steps": [], "run_id": ""}
            if not isinstance(bridge_result, dict):
                bridge_result = {}
            try:
                status, summary, error, steps, _ = _extract_execution_fields(bridge_result)
                await send_to_n8n_with_response({
                    "source": "telegram_agentbridge_result", "client_id": client_id, "chat_id": chat_id, "user_id": user_id,
                    "goal": goal_text, "status": status, "summary": summary, "error": error, "run_id": run_id, "result": bridge_result,
                })
            except Exception as exc:
                capture_exception(exc, {"source": "telegram_agentbridge_result", "chat_id": chat_id})
        bridge_result["run_id"] = bridge_result.get("run_id") or run_id
        status, summary, error, steps, _ = _extract_execution_fields(bridge_result)
        async with _TELEGRAM_RUNS_LOCK:
            if run_id in _TELEGRAM_RUNS:
                _TELEGRAM_RUNS[run_id]["status"] = status
        await _send_telegram_agent_final_reply(chat_id=chat_id, goal_text=goal_text, bridge_result=bridge_result, run_id=run_id)

    task = asyncio.create_task(_execute_and_send())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=float(timeout_sec))
    except asyncio.TimeoutError:
        await send_telegram_message(
            chat_id,
            f"⏳ Running… run_id=`{run_id}`. Te aviso cuando termine.",
        )
        return
    except asyncio.CancelledError:
        raise
    finally:
        if not task.done():
            async with _TELEGRAM_RUNS_LOCK:
                if run_id in _TELEGRAM_RUNS:
                    _TELEGRAM_RUNS[run_id]["status"] = "running"


async def _send_telegram_agent_final_reply(
    *,
    chat_id: str,
    goal_text: str,
    bridge_result: Dict[str, Any],
    run_id: str,
) -> None:
    """Format and send final Telegram reply (used after bridge.run_goal finishes, including from background)."""
    status, summary, error, steps, _ = _extract_execution_fields(dict(bridge_result or {}))
    reply = _format_operator_reply(goal=goal_text, status=status, summary=summary, error=error, steps=steps)
    _LAST_TASK[chat_id] = {
        "goal": goal_text,
        "status": status,
        "summary": summary,
        "steps_total": len(steps),
        "steps_ok": len([s for s in steps if str(s.get("status") or "") == "ok"]),
        "error": error,
        "run_id": run_id,
        "reply": reply,
    }
    _schedule_task_completion_voice(status=status, goal=goal_text, summary=summary, run_id=run_id)
    await send_telegram_message(chat_id, reply)


async def _handle_telegram_command(
    *,
    chat_id: str,
    user_id: str,
    command_text: str,
    update: Dict[str, Any],
) -> bool:
    raw = str(command_text or "").strip()
    if not raw.startswith("/"):
        return False
    cmd_line = raw.split("@", 1)[0]
    parts = cmd_line.split()
    command = parts[0].lower()
    args = parts[1:]

    if command in {"/start", "/help"}:
        await send_telegram_message(chat_id, _telegram_help_text())
        return True

    if command == "/ping":
        await send_telegram_message(chat_id, "pong ✅")
        return True

    if command == "/debug":
        linked = _resolve_telegram_client_id(chat_id)
        bot_token_ok = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
        openai_ok = bool(os.getenv("OPENAI_API_KEY", "").strip().replace("__IN_ENV_SECRETS__", ""))
        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "none")
        ngrok_url = "no corriendo"
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=1.5) as _c:
                _r = await _c.get("http://localhost:4040/api/tunnels")
                _tunnels = _r.json().get("tunnels", [])
                for _t in _tunnels:
                    if _t.get("public_url", "").startswith("https://"):
                        ngrok_url = _t["public_url"]
                        break
        except Exception:
            pass
        tg_webhook_info = "desconocido"
        try:
            _token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            if _token:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=5.0) as _c:
                    _wr = await _c.get(f"https://api.telegram.org/bot{_token}/getWebhookInfo")
                    _wi = _wr.json().get("result", {})
                    _wurl = _wi.get("url", "none")
                    _wpe = _wi.get("pending_update_count", 0)
                    _werr = _wi.get("last_error_message", "")
                    tg_webhook_info = f"url={_wurl}\npending={_wpe}"
                    if _werr:
                        tg_webhook_info += f"\n⚠️ error={_werr}"
        except Exception as _e:
            tg_webhook_info = f"error: {_e}"
        lines = [
            "🔍 *DEBUG — Telegram*",
            f"chat_id: `{chat_id}`",
            f"client_id: `{linked or 'NO VINCULADO'}`",
            f"bot_token: {'✅' if bot_token_ok else '❌ NO SETEADO'}",
            f"openai_key: {'✅' if openai_ok else '❌ NO SETEADO'}",
            f"agent_mode: `{_telegram_agent_mode()}`",
            f"flow_mode: `{_telegram_flow_mode()}`",
            f"ngrok: `{ngrok_url}`",
            f"webhook_env: `{webhook_url}`",
            f"telegram_webhook:\n`{tg_webhook_info}`",
        ]
        await send_telegram_message(chat_id, "\n".join(lines))
        return True

    if command == "/doctor":
        linked_client_id = _resolve_telegram_client_id(chat_id)
        try:
            from brain.doctor import run_doctor_checks
            result = await run_doctor_checks(chat_id=chat_id, client_id=linked_client_id or None)
        except Exception as exc:
            await send_telegram_message(
                chat_id,
                f"❌ Doctor error: {str(exc)[:200]}",
            )
            return True
        openai_ok = (result.get("openai") or {}).get("ok") is True
        tg_ok = (result.get("telegram_send") or {}).get("ok") is True
        n8n_ok = (result.get("n8n_webhook") or {}).get("ok") is True
        runner = result.get("runner") or {}
        runner_connected = runner.get("connected") is True
        cloud = result.get("cloud_vs_local", "local")
        db_issues = result.get("db_issues") or []
        summary_ok = result.get("summary_ok") is True
        lines = [
            "🩺 *Doctor*",
            f"OpenAI: {'✅' if openai_ok else '❌'}",
            f"Telegram send: {'✅' if tg_ok else '❌'}",
            f"n8n webhook: {'✅' if n8n_ok else '❌'}",
            f"Runner: {'✅ conectado' if runner_connected else '❌ offline'}",
            f"Entorno: {cloud}",
        ]
        if db_issues:
            lines.append(f"DB: ⚠️ {len(db_issues)} issue(s)")
        lines.append(f"Resumen: {'✅ OK' if summary_ok else '❌ Revisar'}")
        import json
        brief = {
            "openai": openai_ok,
            "n8n": n8n_ok,
            "runner": runner_connected,
            "cloud_vs_local": cloud,
        }
        lines.append(f"`{json.dumps(brief)}`")
        await send_telegram_message(chat_id, "\n".join(lines))
        return True

    if command == "/runner":
        linked_client_id = _resolve_telegram_client_id(chat_id)
        client_id = linked_client_id or str(os.getenv("JARVIS_CLIENT_ID") or os.getenv("TELEGRAM_DEFAULT_CLIENT_ID") or "")
        try:
            from brain.doctor import _runner_connected
            runner = await _runner_connected(client_id or None)
        except Exception as exc:
            await send_telegram_message(chat_id, f"❌ Runner check error: {str(exc)[:150]}")
            return True
        base_url = (os.getenv("BACKEND_URL") or os.getenv("API_URL") or "").rstrip("/")
        ws_url = f"{base_url}/desktop-agent/ws" if base_url else "(configura BACKEND_URL o API_URL)"
        connected = runner.get("connected") is True
        lines = [
            "🖥 *Runner (desktop)*",
            f"Conectado: {'✅ Sí' if connected else '❌ No'}",
            f"client_id: `{client_id or '(ninguno)'}`",
            f"last_heartbeat: (ver /doctor)",
            f"ws_url: `{ws_url}`",
        ]
        if not connected and client_id:
            from execution.desktop_executor import runner_offline_instructions
            instr = runner_offline_instructions(base_url or None)
            lines.append("")
            lines.append("Para conectar el Runner en tu Mac:")
            lines.append(instr[:600] + "…" if len(instr) > 600 else instr)
        await send_telegram_message(chat_id, "\n".join(lines))
        return True

    if command == "/status":
        if args:
            run_id = str(args[0]).strip()
            if _TELEGRAM_RUNS_LOCK is None:
                await send_telegram_message(chat_id, f"run_id `{run_id}`: no registry.")
            else:
                async with _TELEGRAM_RUNS_LOCK:
                    r = _TELEGRAM_RUNS.get(run_id)
                if not r:
                    await send_telegram_message(chat_id, f"run_id `{run_id}`: no encontrado.")
                else:
                    await send_telegram_message(
                        chat_id,
                        f"run_id: `{run_id}`\nstatus: {r.get('status', '?')}\ngoal: {str(r.get('goal', ''))[:60]}",
                    )
            return True
        linked_client_id = _resolve_telegram_client_id(chat_id)
        reply = (
            f"chat_id={chat_id}\n"
            f"linked_client_id={linked_client_id or 'none'}\n"
            f"agent_mode={_telegram_agent_mode()}\n"
            f"flow_mode={_telegram_flow_mode()}\n"
            f"telegram_reply_enabled={os.getenv('ENABLE_TELEGRAM_REPLY', 'false')}\n"
            f"webhook_mode=enabled"
        )
        await send_telegram_message(chat_id, reply)
        return True

    if command == "/runs":
        if _TELEGRAM_RUNS_LOCK is None:
            await send_telegram_message(chat_id, "No hay runs registrados.")
        else:
            async with _TELEGRAM_RUNS_LOCK:
                runs = list(_TELEGRAM_RUNS.items())
            if not runs:
                await send_telegram_message(chat_id, "No hay runs activos.")
            else:
                lines = [f"run_id=`{rid}` status={r.get('status')} goal={str(r.get('goal', ''))[:40]}" for rid, r in runs[-10:]]
                await send_telegram_message(chat_id, "\n".join(lines))
        return True

    if command == "/stop":
        if not args:
            await send_telegram_message(chat_id, "Uso: /stop <run_id>")
            return True
        run_id = str(args[0]).strip()
        if _TELEGRAM_RUNS_LOCK is None:
            await send_telegram_message(chat_id, f"run_id `{run_id}`: no se puede detener (sin registry).")
        else:
            async with _TELEGRAM_RUNS_LOCK:
                r = _TELEGRAM_RUNS.get(run_id)
            if not r:
                await send_telegram_message(chat_id, f"run_id `{run_id}`: no encontrado.")
            else:
                # Mark as cancelled; the running task may still complete but we don't send again if cancelled
                async with _TELEGRAM_RUNS_LOCK:
                    _TELEGRAM_RUNS[run_id]["status"] = "cancelled"
                await send_telegram_message(chat_id, f"run_id `{run_id}` marcado como cancelado.")
        return True

    if command == "/unlink":
        _RUNTIME_TELEGRAM_CHAT_MAP.pop(chat_id, None)
        await send_telegram_message(chat_id, "Vinculo temporal removido de este runtime.")
        return True

    if command == "/link":
        if not args:
            await send_telegram_message(chat_id, "Uso: /link <organization_uuid> [codigo]")
            return True
        proposed = str(args[0]).strip()
        required_code = str(os.getenv("TELEGRAM_LINK_CODE") or "").strip()
        provided_code = str(args[1]).strip() if len(args) > 1 else ""
        if required_code and provided_code != required_code:
            await send_telegram_message(chat_id, "Codigo de enlace invalido.")
            return True
        if not await _client_exists(proposed):
            await send_telegram_message(chat_id, "organization_uuid no existe o es invalido.")
            return True
        _RUNTIME_TELEGRAM_CHAT_MAP[chat_id] = proposed
        await send_telegram_message(chat_id, f"Chat vinculado correctamente a {proposed}.")
        return True

    if command == "/memory":
        client_id = _resolve_telegram_client_id(chat_id)
        if not client_id:
            await send_telegram_message(chat_id, "Este chat aun no esta vinculado. Usa /link.")
            return True
        summary = specialist_memory.summary(client_id=client_id, specialist="telegram")
        runs = int(summary.get("runs", 0))
        success_rate = float(summary.get("success_rate", 0.0))
        layers = dict(summary.get("memory_layers") or {})
        reply = (
            f"Memoria telegram\n"
            f"runs={runs}\n"
            f"success_rate={success_rate:.2f}\n"
            f"episodic={int(layers.get('episodic', 0))}\n"
            f"semantic={int(layers.get('semantic', 0))}\n"
            f"task={int(layers.get('task', 0))}"
        )
        await send_telegram_message(chat_id, reply)
        return True

    if command == "/run":
        client_id = _resolve_telegram_client_id(chat_id)
        if not client_id:
            await send_telegram_message(chat_id, "Error de autenticación: ID no encontrado")
            return True
        task = str(raw[len("/run"):]).strip()
        if not task:
            await send_telegram_message(chat_id, "Uso: /run <instruccion>")
            return True
        await _process_message(
            client_id=client_id,
            session_id=f"telegram:{chat_id}",
            message=task,
            channel="telegram",
            user_id=user_id,
            inbound_payload=update,
        )
        return True

    if command == "/agent":
        client_id = _resolve_telegram_client_id(chat_id)
        if not client_id:
            await send_telegram_message(chat_id, "Error de autenticación: ID no encontrado")
            return True
        goal = str(raw[len("/agent"):]).strip()
        if not goal:
            await send_telegram_message(chat_id, "Uso: /agent <objetivo>")
            return True
        await send_telegram_message(chat_id, "En marcha. Ejecutando modo agente.")
        await _run_telegram_agent(
            client_id=client_id,
            chat_id=chat_id,
            user_id=user_id,
            goal=goal,
            update=update,
        )
        return True

    await send_telegram_message(chat_id, "Comando no reconocido. Usa /help.")
    return True


def _extract_meta_text_messages(payload: Dict[str, Any]) -> list[Dict[str, str]]:
    out: list[Dict[str, str]] = []
    for entry in list(payload.get("entry") or []):
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get("id") or "").strip()
        for item in list(entry.get("messaging") or []):
            if not isinstance(item, dict):
                continue
            text = str(((item.get("message") or {}).get("text") or "")).strip()
            sender_id = str((item.get("sender") or {}).get("id") or "").strip()
            if text and sender_id:
                out.append({"page_id": page_id, "sender_id": sender_id, "text": text})
        for change in list(entry.get("changes") or []):
            if not isinstance(change, dict):
                continue
            value = dict(change.get("value") or {})
            phone_id = str((value.get("metadata") or {}).get("phone_number_id") or "").strip()
            for msg in list(value.get("messages") or []):
                if not isinstance(msg, dict):
                    continue
                text = str(((msg.get("text") or {}).get("body") or "")).strip()
                sender_id = str(msg.get("from") or "").strip()
                if text and sender_id:
                    out.append({"page_id": phone_id or page_id, "sender_id": sender_id, "text": text})
    return out


async def _emit_business_event(
    *,
    client_id: str,
    event_type: str,
    source: str,
    channel: Optional[str],
    entity_id: Optional[str],
    payload: Dict[str, Any],
) -> None:
    try:
        from uuid import UUID

        from brain.business_monitor import ingest_business_event

        org_id = UUID(client_id)
    except Exception:
        return
    try:
        async with AsyncSessionLocal() as session:
            await ingest_business_event(
                session,
                organization_id=org_id,
                event_type=event_type,
                source=source,
                channel=channel,
                entity_id=entity_id,
                payload=payload,
            )
    except Exception:
        return


async def _should_auto_approve(action: ActionContract, *, channel: str) -> bool:
    _ = channel
    # Keep existing autonomy behavior permissive at runtime level.
    return not requires_human_approval(action)


def _validate_action(payload: Any) -> Optional[ActionContract]:
    if not isinstance(payload, dict):
        return None
    try:
        return ActionContract.model_validate(payload)
    except Exception:
        return None


def _build_action_policy(result: Dict[str, Any]) -> Dict[str, Any]:
    policy = {"contract_valid": False, "requires_human_approval": False}
    if str(result.get("type") or "") != "action":
        return policy
    action_model = _validate_action(result.get("payload"))
    if not action_model:
        return policy
    policy["contract_valid"] = True
    policy["requires_human_approval"] = bool(requires_human_approval(action_model))
    return policy


async def _dispatch_client_ai(client_id: str, payload: Dict[str, Any]) -> None:
    message = str(payload.get("message") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not message or not session_id:
        return
    try:
        engine = CognitiveEngine.get_instance()
    except Exception as exc:
        capture_exception(exc, {"source": "client_ai", "stage": "init_engine"})
        return
    try:
        result = await engine.generate_response(client_id=client_id, session_id=session_id, message=message)
        await send_to_n8n_with_response(
            {
                "source": "client_ai",
                "client_id": client_id,
                "session_id": session_id,
                "message": message,
                "result": result,
                "action_policy": _build_action_policy(result),
                "metadata": dict(payload.get("metadata") or {}),
            }
        )
    except Exception as exc:
        capture_exception(exc, {"source": "client_ai", "stage": "generate", "client_id": client_id})


async def _dispatch_meta_ai(payload: Dict[str, Any]) -> None:
    if os.getenv("ENABLE_META_AI", "false").lower() not in {"1", "true", "yes"}:
        return
    mapping = _load_meta_page_client_map()
    if not mapping:
        return
    try:
        engine = CognitiveEngine.get_instance()
    except Exception as exc:
        capture_exception(exc, {"source": "meta_ai", "stage": "init_engine"})
        return
    for item in _extract_meta_text_messages(payload):
        page_id = str(item.get("page_id") or "")
        sender_id = str(item.get("sender_id") or "")
        text = str(item.get("text") or "")
        client_id = _resolve_client_id(mapping, page_id)
        if not client_id:
            continue
        try:
            result = await engine.generate_response(
                client_id=client_id,
                session_id=f"meta:{sender_id}",
                message=text,
            )
            await send_to_n8n_with_response(
                {
                    "source": "meta_ai",
                    "client_id": client_id,
                    "session_id": f"meta:{sender_id}",
                    "message": text,
                    "result": result,
                    "action_policy": _build_action_policy(result),
                }
            )
        except Exception as exc:
            capture_exception(
                exc,
                {
                    "source": "meta_ai",
                    "stage": "generate",
                    "page_id": page_id,
                    "sender_id": sender_id,
                },
            )


async def _process_message(
    *,
    client_id: str,
    session_id: str,
    message: str,
    channel: str,
    user_id: Optional[str],
    inbound_payload: Dict[str, Any],
) -> None:
    telegram_chat_id = ""
    if channel == "telegram" and session_id.startswith("telegram:"):
        telegram_chat_id = session_id.split(":", 1)[1].strip()
    engine = CognitiveEngine.get_instance()
    try:
        result = await engine.generate_response(
            client_id=client_id,
            session_id=session_id,
            message=message,
        )
    except ValueError as exc:
        if "Client not found" in str(exc):
            if telegram_chat_id:
                await send_telegram_message(
                    telegram_chat_id,
                    "No hay una organización válida para este chat. Usa /link <organization_uuid> o crea el cliente en la base de datos (JARVIS_CLIENT_ID).",
                )
            return
        raise
    except Exception as exc:
        raise
    result = result or {}
    slack_channel_id = ""
    if channel == "slack" and session_id.startswith("slack:"):
        slack_channel_id = session_id.split(":", 1)[1].strip()
    discord_channel_id = ""
    if channel == "discord" and session_id.startswith("discord:"):
        discord_channel_id = session_id.split(":", 1)[1].strip()
    whatsapp_to = ""
    whatsapp_from = ""
    if channel == "whatsapp":
        ycloud = dict(inbound_payload.get("_ycloud") or {})
        whatsapp_to = str(ycloud.get("reply_to") or ycloud.get("from") or "").strip()
        whatsapp_from = str(ycloud.get("send_from") or ycloud.get("to") or "").strip()
    teams_webhook = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
    await _emit_business_event(
        client_id=client_id,
        event_type="message_in",
        source=f"{channel}.event",
        channel=channel,
        entity_id=session_id,
        payload={"message": message, "user_id": user_id, "raw": inbound_payload},
    )
    await _emit_business_event(
        client_id=client_id,
        event_type="message_out",
        source=f"{channel}.event",
        channel=channel,
        entity_id=session_id,
        payload={"result_type": result.get("type"), "content": result.get("content")},
    )

    action_model = _validate_action(result.get("payload"))
    if result.get("type") == "action" and action_model:
        goal_tracker.register_goal(
            client_id=client_id,
            goal_text=str(action_model.rationale or message),
            action_type=str(action_model.action_type or "integration"),
            action_name=str(action_model.action or "action"),
            metadata={"channel": channel, "session_id": session_id},
        )
        auto_approved = await _should_auto_approve(action_model, channel=channel)
        await send_to_n8n_with_response(
            {
                "source": f"{channel}_action",
                "client_id": client_id,
                "session_id": session_id,
                "channel": channel,
                "message": message,
                "action": action_model.model_dump(),
                "approval": {
                    "required": not auto_approved,
                    "status": "approved" if auto_approved else "pending",
                },
            }
        )
        approval_required = not auto_approved
        action_reply = (
            "Listo. Ejecute la accion automaticamente."
            if not approval_required
            else "Recibido. La accion requiere aprobacion humana."
        )
        if telegram_chat_id:
            await send_telegram_message(telegram_chat_id, action_reply)
        if slack_channel_id:
            await send_slack_message(slack_channel_id, action_reply)
        if discord_channel_id:
            await send_discord_message(discord_channel_id, action_reply)
        if channel == "whatsapp" and whatsapp_to and whatsapp_from:
            await send_ycloud_whatsapp_text_message(
                from_number=whatsapp_from,
                to=whatsapp_to,
                text=action_reply,
            )
        if channel == "teams" and teams_webhook:
            await send_teams_message(teams_webhook, action_reply)
        return

    # Optional text echo relay for downstream integrations.
    await send_to_n8n_with_response(
        {
            "source": f"{channel}_message",
            "client_id": client_id,
            "session_id": session_id,
            "channel": channel,
            "content": result.get("content", ""),
        }
    )
    content = str(result.get("content") or "").strip() or "Listo."

    if channel == "whatsapp":
        try:
            from brain.humanizer import humanize_text

            content = humanize_text(content, channel="whatsapp")
        except Exception:
            logger.warning(
                "[HUMANIZER] dispatcher integration failed (non-blocking)",
                exc_info=True,
            )

    if telegram_chat_id:
        await send_telegram_message(telegram_chat_id, content)
    if slack_channel_id:
        await send_slack_message(slack_channel_id, content)
    if discord_channel_id:
        await send_discord_message(discord_channel_id, content)
    if channel == "whatsapp" and whatsapp_to and whatsapp_from:
        await send_ycloud_whatsapp_text_message(
            from_number=whatsapp_from,
            to=whatsapp_to,
            text=content,
        )
    if channel == "teams" and teams_webhook:
        await send_teams_message(teams_webhook, content)


def _extract_ycloud_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload.get("data") or {})
    candidates = (
        payload.get("whatsappInboundMessage"),
        payload.get("whatsappMessage"),
        payload.get("message"),
        data.get("whatsappInboundMessage"),
        data.get("whatsappMessage"),
        data.get("message"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def _extract_ycloud_text(message: Dict[str, Any]) -> str:
    text_block = message.get("text")
    if isinstance(text_block, dict):
        body = str(text_block.get("body") or "").strip()
        if body:
            return body
    if isinstance(text_block, str) and text_block.strip():
        return text_block.strip()
    interactive = dict(message.get("interactive") or {})
    for key in ("button_reply", "list_reply"):
        block = dict(interactive.get(key) or {})
        probe = str(block.get("title") or block.get("id") or "").strip()
        if probe:
            return probe
    return ""


def _resolve_ycloud_client_id(message: Dict[str, Any]) -> Optional[str]:
    mapping = _load_ycloud_channel_client_map()
    for key in (
        str(message.get("to") or "").strip(),
        str(message.get("wabaId") or "").strip(),
        str(message.get("whatsappChannelId") or "").strip(),
    ):
        if not key:
            continue
        client_id = _resolve_client_id(mapping, key)
        if client_id:
            return client_id
    for fallback_env in ("YCLOUD_DEFAULT_CLIENT_ID", "JARVIS_CLIENT_ID", "KAN_CLIENT_ID"):
        value = str(os.getenv(fallback_env) or "").strip()
        if value:
            return value
    return None


async def _transcribe_voice_message(message: Dict[str, Any]) -> Optional[str]:
    """Download and transcribe a Telegram voice/audio message via Whisper."""
    voice = message.get("voice") or message.get("audio")
    if not voice:
        return None
    file_id = str((voice if isinstance(voice, dict) else {}).get("file_id") or "").strip()
    if not file_id:
        return None
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return None
    try:
        from tools.whisper_client import transcribe_telegram_voice
        transcript = await transcribe_telegram_voice(file_id, bot_token)
        return transcript
    except Exception as exc:
        logger.warning("Voice transcription failed: %s", exc)
        return None


async def handle_telegram_event(update: Dict[str, Any]) -> None:
    # "channel_post" is used when the bot is admin of a Telegram channel; "message" for groups/private
    message = dict(update.get("message") or update.get("channel_post") or {})

    # --- Voice transcription: if audio/voice note, transcribe before anything else ---
    transcribed_text: Optional[str] = None
    if message.get("voice") or message.get("audio"):
        transcribed_text = await _transcribe_voice_message(message)

    text = transcribed_text or _extract_telegram_message_text(message)
    if not text:
        return
    chat_id = str((message.get("chat") or {}).get("id") or "").strip()
    if not chat_id:
        return
    logger.info("[TELEGRAM] Orden recibida: %r", text)
    if os.getenv("TELEGRAM_TRACEBACK_LOG", "false").strip().lower() in {"1", "true", "yes"}:
        logger.info("[TELEGRAM] Traceback:\n%s", "".join(traceback.format_stack(limit=10)))
    user_id = str((message.get("from") or {}).get("id") or "")
    if text.startswith("/"):
        handled = await _handle_telegram_command(
            chat_id=chat_id,
            user_id=user_id,
            command_text=text,
            update=update,
        )
        if handled:
            return
    client_id = _resolve_telegram_client_id(chat_id)
    if not client_id:
        _log_bridge_start("Telegram", client_id="None", text=text)
        await send_telegram_message(
            chat_id,
            "Error de autenticación: ID no encontrado",
        )
        return
    _log_bridge_start("Telegram", client_id=client_id, text=text)
    try:
        # Responder consultas de estado con el resultado de la última tarea
        if _looks_like_status_query(text) and chat_id in _LAST_TASK:
            last = _LAST_TASK[chat_id]
            _STATUS_EMOJI = {"completed": "✅", "failed": "❌", "blocked": "⛔", "dry_run": "🔍"}
            emoji = _STATUS_EMOJI.get(last["status"], "⚙️")
            summary = last.get("summary") or ""
            status_line = f"{emoji} Última tarea: *{last['status'].upper()}*"
            goal_line = f"Objetivo: _{last['goal'][:80]}..._" if len(last.get("goal", "")) > 80 else f"Objetivo: _{last.get('goal', '')}_"
            steps_line = f"Pasos: {last.get('steps_ok', 0)}/{last.get('steps_total', 0)} completados"
            parts = [status_line, goal_line, steps_line]
            if summary:
                parts.append(summary)
            if last.get("error"):
                parts.append(f"⚠️ Error: {last['error']}")
            await send_telegram_message(chat_id, "\n".join(parts))
            return

        if _telegram_flow_mode() == "agentbridge_direct" and _should_run_agentbridge_direct(text):
            await send_telegram_message(chat_id, _autonomous_ack(text))
            publish_state("thinking", source="telegram", text=text[:220], level=0.7)
            try:
                await _run_telegram_agent(
                    client_id=client_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    goal=text,
                    update=update,
                )
            finally:
                publish_state("standby", source="telegram", text="", level=0.15)
            return
        if _telegram_flow_mode() == "agentbridge_direct":
            await _process_message(
                client_id=client_id,
                session_id=f"telegram:{chat_id}",
                message=text,
                channel="telegram",
                user_id=user_id,
                inbound_payload=update,
            )
            return

        mode = _telegram_agent_mode()
        if mode == "autonomous" and _looks_like_agent_followup(text):
            previous_goal = str((_LAST_TASK.get(chat_id) or {}).get("goal") or "").strip()
            followup_goal = previous_goal if previous_goal else "Ejecuta la ultima intencion del usuario"
            followup_goal = f"{followup_goal}\n\nFollow-up del usuario: {text}"
            await send_telegram_message(chat_id, _autonomous_ack(text))
            publish_state("thinking", source="telegram", text=followup_goal[:220], level=0.7)
            try:
                await _run_telegram_agent(
                    client_id=client_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    goal=followup_goal,
                    update=update,
                )
            finally:
                publish_state("standby", source="telegram", text="", level=0.15)
            return
        if mode == "autonomous" and _looks_like_agent_goal(text):
            await send_telegram_message(chat_id, "Entendido. Procediendo con modo agente.")
            publish_state("thinking", source="telegram", text=text[:220], level=0.7)
            try:
                await _run_telegram_agent(
                    client_id=client_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    goal=text,
                    update=update,
                )
            finally:
                publish_state("standby", source="telegram", text="", level=0.15)
            return
        await _process_message(
            client_id=client_id,
            session_id=f"telegram:{chat_id}",
            message=text,
            channel="telegram",
            user_id=user_id,
            inbound_payload=update,
        )
    except Exception as exc:
        logger.exception("Failed to handle Telegram event")
        capture_exception(exc, {"source": "telegram", "chat_id": chat_id})
        await send_telegram_message(
            chat_id,
            "Recibi tu mensaje, pero tuve un error interno al procesarlo. Intenta de nuevo.",
        )


async def handle_slack_event(payload: Dict[str, Any]) -> None:
    event = dict(payload.get("event") or {})
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return
    text = str(event.get("text") or "").strip()
    if not text:
        return
    channel = str(event.get("channel") or "").strip()
    if not channel:
        return
    client_id = _resolve_client_id(_load_map("SLACK_CHANNEL_CLIENT_MAP_JSON"), channel)
    if not client_id:
        return
    try:
        await _process_message(
            client_id=client_id,
            session_id=f"slack:{channel}",
            message=text,
            channel="slack",
            user_id=str(event.get("user") or ""),
            inbound_payload=payload,
        )
    except Exception as exc:
        logger.exception("Failed to handle Slack event")
        capture_exception(exc, {"source": "slack", "channel": channel})


async def handle_meta_event(payload: Dict[str, Any]) -> None:
    """Entry point for Meta (WhatsApp/Instagram) events."""
    try:
        await send_to_n8n_with_response({"source": "meta_raw", "data": payload})
        await _dispatch_meta_ai(payload)
    except Exception as exc:
        logger.exception("Failed to handle Meta event")
        capture_exception(exc, {"source": "meta"})


_QUALIFIED_REPLY_TOKENS = frozenset({
    "precio", "costo", "cuanto", "cuánto", "tarifa", "cotizacion", "cotización",
    "agenda", "llamada", "reunion", "reunión", "cita", "agendar",
    "interesa", "quiero", "necesito", "busco",
    "demo", "prueba", "diagnostico", "diagnóstico",
    "servicio", "plan", "paquete",
    "contratar", "comprar", "invertir",
    "disponibilidad", "horario",
    "asesoria", "asesoría", "consulta",
})


def _is_commercially_qualified(text: str) -> bool:
    """Fast keyword check — True when the message shows commercial intent."""
    words = str(text or "").lower().split()
    return any(w in _QUALIFIED_REPLY_TOKENS for w in words)


async def handle_ycloud_event(payload: Dict[str, Any]) -> None:
    supported_event_types = {
        "whatsapp.inbound.message",
        "whatsapp.inbound_message.received",
    }
    event_type = str(
        payload.get("type")
        or payload.get("eventType")
        or ((payload.get("data") or {}).get("type"))
        or ""
    ).strip()
    if event_type and event_type not in supported_event_types:
        return

    message = _extract_ycloud_message(payload)
    text = _extract_ycloud_text(message)
    if not text:
        return

    sender = str(message.get("from") or "").strip()
    if not sender:
        return

    client_id = _resolve_ycloud_client_id(message)
    if not client_id:
        return

    inbound_payload = dict(payload)
    inbound_payload["_ycloud"] = {
        "from": sender,
        "to": str(message.get("to") or "").strip(),
        "reply_to": sender,
        "send_from": str(message.get("to") or "").strip(),
        "message_id": str(message.get("id") or "").strip(),
    }

    provider_msg_id = str(message.get("id") or "").strip()

    try:
        from brain.creative_event_tracker import (
            on_conversation_started,
            on_reply,
        )

        on_conversation_started(
            lead_id=sender, channel="whatsapp", raw_text=text,
        )
        on_reply(
            sender, "whatsapp",
            is_qualified=_is_commercially_qualified(text),
            message_id=provider_msg_id,
            text=text,
        )
        logger.info(
            "[CREATIVE_TRACKER] ycloud tracked sender=%s qualified=%s "
            "msg_id=%s",
            sender,
            _is_commercially_qualified(text),
            provider_msg_id or "(none)",
        )
    except Exception:
        logger.warning(
            "[CREATIVE_TRACKER] ycloud tracking skipped safely "
            "sender=%s (non-blocking)",
            sender,
            exc_info=True,
        )

    try:
        await _process_message(
            client_id=client_id,
            session_id=f"whatsapp:{sender}",
            message=text,
            channel="whatsapp",
            user_id=sender,
            inbound_payload=inbound_payload,
        )
    except Exception as exc:
        logger.exception("Failed to handle YCloud event")
        capture_exception(exc, {"source": "ycloud", "sender": sender})


async def handle_client_event(client_id: str, payload: Dict[str, Any]) -> None:
    """Entry point for internal client webhooks (non-Meta)."""
    try:
        await send_to_n8n_with_response({"source": "client_raw", "client_id": client_id, "data": payload})
        mode = str(payload.get("mode") or "").strip().lower()
        if mode == "chat":
            await _dispatch_client_ai(client_id, payload)
    except Exception as exc:
        logger.exception("Failed to handle client event")
        capture_exception(exc, {"source": "client", "client_id": client_id})


async def handle_shopify_event(payload: Dict[str, Any], topic: str, shop: str) -> None:
    """Entry point for Shopify webhooks."""
    try:
        await send_to_n8n_with_response({"source": "shopify", "topic": topic, "shop": shop, "data": payload})
    except Exception as exc:
        logger.exception("Failed to handle Shopify event")
        capture_exception(exc, {"source": "shopify", "topic": topic, "shop": shop})


async def handle_discord_event(payload: Dict[str, Any]) -> None:
    """Entry point for Discord interaction/message events.

    Supports:
    - type=2 (APPLICATION_COMMAND): slash command with options[0].value as text
    - type=3 (MESSAGE_COMPONENT): button/select interactions (ignored for now)
    - MESSAGE_CREATE events from Gateway bot (dispatched by the bot process)
    """
    interaction_type = payload.get("type")
    channel_id = str(payload.get("channel_id") or "").strip()

    # Slash command (APPLICATION_COMMAND)
    if interaction_type == 2:
        data = dict(payload.get("data") or {})
        options = list(data.get("options") or [])
        text = str((options[0].get("value") if options else None) or "").strip()
        if not text:
            # Try name of command as fallback
            text = str(data.get("name") or "").strip()
        user = dict((payload.get("member") or {}).get("user") or payload.get("user") or {})
        user_id = str(user.get("id") or "").strip()
    else:
        # MESSAGE_CREATE style (from bot gateway relay)
        text = str(payload.get("content") or "").strip()
        author = dict(payload.get("author") or {})
        if author.get("bot"):
            return
        user_id = str(author.get("id") or "").strip()

    if not text or not channel_id:
        return

    client_id = _resolve_client_id(
        _load_map("DISCORD_CHANNEL_CLIENT_MAP_JSON"), channel_id
    )
    if not client_id:
        return

    try:
        await _process_message(
            client_id=client_id,
            session_id=f"discord:{channel_id}",
            message=text,
            channel="discord",
            user_id=user_id,
            inbound_payload=payload,
        )
    except Exception as exc:
        logger.exception("Failed to handle Discord event")
        capture_exception(exc, {"source": "discord", "channel_id": channel_id})


async def handle_teams_event(payload: Dict[str, Any]) -> None:
    """Entry point for Microsoft Teams Bot Framework activities.

    Handles 'message' activities; ignores conversationUpdate and other types.
    """
    activity_type = str(payload.get("type") or "").lower()
    if activity_type != "message":
        return

    text = str(payload.get("text") or "").strip()
    if not text:
        return

    # channel_data.teamsChannelId is the stable Teams channel identifier
    channel_data = dict(payload.get("channelData") or {})
    channel_id = str(channel_data.get("teamsChannelId") or "").strip()
    if not channel_id:
        # Fallback: conversation.id
        channel_id = str((payload.get("conversation") or {}).get("id") or "").strip()
    if not channel_id:
        return

    from_obj = dict(payload.get("from") or {})
    user_id = str(from_obj.get("id") or "").strip()
    # Ignore bot messages
    if from_obj.get("role") == "bot":
        return

    client_id = _resolve_client_id(
        _load_map("TEAMS_CHANNEL_CLIENT_MAP_JSON"), channel_id
    )
    if not client_id:
        return

    # For Teams replies we use the serviceUrl + conversation reference
    # stored in payload; for simplicity we relay via TEAMS_WEBHOOK_URL
    try:
        await _process_message(
            client_id=client_id,
            session_id=f"teams:{channel_id}",
            message=text,
            channel="teams",
            user_id=user_id,
            inbound_payload=payload,
        )
    except Exception as exc:
        logger.exception("Failed to handle Teams event")
        capture_exception(exc, {"source": "teams", "channel_id": channel_id})
