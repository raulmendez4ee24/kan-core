"""
AgentBridge — Motor ReAct de grado superior para K'an.

Ciclo: Think → Plan → Act → Observe (× max_iterations) → FINAL

Diferencias vs. AutonomousAgent anterior:
- LLM directo via httpx (sin session DB overhead)
- ToolBox con acceso real al sistema macOS (bash, screen, browser)
- Self-healing: errores se retroalimentan al LLM en el siguiente paso
- Respuesta ejecutiva estilo Jarvis
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger("kan_core.agent_bridge")

_FAILURE_PREFIXES = ("[exit ", "[error", "[timeout", "[bash_error", "[tool_error", "[screenshot_error]")
_SYSTEM_TASK_HINTS = (
    "abre ", "abrir ", "open ",
    "cierra ", "cerrar ", "close ",
    "chrome", "safari", "finder", "terminal", "vscode", "visual studio code",
)
_WEB_TASK_HINTS = (
    "http://",
    "https://",
    "www.",
    "browser",
    "navegador",
    "web ",
    "en la web",
    "sitio",
    "pagina",
    "página",
    "formulario",
    "login",
    "dashboard",
    "portal",
    "busca en",
    "search on",
)
_WEB_URL_RE = re.compile(r"(https?://[^\s)]+|www\.[^\s)]+)", re.IGNORECASE)
_APP_ALIASES: Dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "safari": "Safari",
    "finder": "Finder",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "iterm2": "iTerm",
    "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code": "Visual Studio Code",
}
_DEBUG_TOKEN_RE = re.compile(
    r"(?i)^\s*(quality_score|event_run_id|run_id|trace_id|debug|metadata|internal_notes?)\s*[:=].*$"
)
_DEBUG_PREFIX_RE = re.compile(
    r"(?i)^\s*(quality_score|event_run_id|run_id|trace_id|debug|metadata|internal_notes?)\s*[:=]\s*([^\s,;|]+)\s*(.*)$"
)


def _bridge_timeout_seconds(goal: str) -> int:
    configured = str(os.getenv("AGENT_BRIDGE_TIMEOUT_SEC") or "").strip()
    if configured:
        try:
            return max(1, min(int(configured), 900))
        except ValueError:
            pass

    # Default resilient timeout for multi-step autonomous execution.
    base = max(30, int(str(os.getenv("AGENT_BRIDGE_TIMEOUT_DEFAULT_SEC") or "120")))
    probe = str(goal or "").strip().lower()
    if len(probe.split()) >= 14:
        base = max(base, 150)
    if _WEB_URL_RE.search(probe) or any(token in probe for token in _WEB_TASK_HINTS):
        base = max(base, 180)
    if any(token in probe for token in _SYSTEM_TASK_HINTS):
        base = max(base, 150)
    return min(base, 900)

# ── Catálogo de herramientas para el prompt ───────────────────────────────────
_TOOLS_CATALOGUE = """
HERRAMIENTAS DISPONIBLES:
- bash        | args: {"command": "..."}         | Ejecuta cualquier comando shell en macOS. Úsalo para abrir apps, consultar el sistema, mover archivos, etc.
- open_url    | args: {"url": "..."}             | Abre una URL en el navegador por defecto.
- screenshot  | args: {}                          | Captura la pantalla. Úsalo SOLO cuando necesitas contexto visual que bash no puede darte.
- running_apps| args: {}                          | Lista las aplicaciones corriendo actualmente.
- type_text   | args: {"text": "..."}            | Escribe texto en la aplicación activa (usa después de enfocarla con bash/AppleScript).
- key_press   | args: {"keys": "cmd+space"}      | Presiona una combinación de teclas.
""".strip()

_SYSTEM_PROMPT = """Eres K'an, un agente autónomo ejecutivo en macOS. Completas tareas usando herramientas directas, sin rodeos.

{tools}

FORMATO DE RESPUESTA — elige exactamente uno:

Para usar una herramienta:
THOUGHT: <razonamiento breve en una línea>
TOOL: <nombre>
ARGS: {{"clave": "valor"}}

Para terminar (tarea completa o imposible):
THOUGHT: <razonamiento>
FINAL: <respuesta ejecutiva para el usuario, máx 2 oraciones, estilo Jarvis>

ESTRATEGIA:
1. Prefiere bash() siempre — es instantáneo y directo.
2. Para abrir apps: usa `open -a 'NombreApp'`.
3. Para consultar el sistema: usa `ps aux`, `ls`, `system_profiler`, etc.
4. Solo usa screenshot() si necesitas ver qué hay en pantalla y bash no puede darte esa info.
5. Si bash falla (exit code != 0), lee el error y prueba una alternativa en la siguiente iteración.
6. La respuesta FINAL debe ser concisa, informativa y en español.
""".format(tools=_TOOLS_CATALOGUE)


# ── Dataclass de paso ─────────────────────────────────────────────────────────
@dataclass
class ReActStep:
    iteration: int
    thought: str
    tool: str
    args: Dict[str, Any]
    observation: str
    success: bool = True


# ── ToolBox ───────────────────────────────────────────────────────────────────
class ToolBox:
    """Acceso directo al sistema macOS. Sin intermediarios, sin abstracciones extra."""

    async def bash(self, command: str, *, timeout: float = 15.0) -> str:
        if not command or not command.strip():
            return "[error] Comando vacío"
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                msg = err or out or "sin salida"
                return f"[exit {proc.returncode}] {msg[:400]}"
            return out[:800] or "[ok — sin salida]"
        except asyncio.TimeoutError:
            return f"[timeout] El comando tardó más de {timeout}s"
        except Exception as exc:
            return f"[bash_error] {exc}"

    async def open_url(self, url: str) -> str:
        if not url.strip():
            return "[error] URL vacía"
        return await self.bash(f"open '{url.strip()}'")

    async def screenshot(self) -> str:
        try:
            from desktop_agent.screen_capture import take_screenshot
            payload = await asyncio.to_thread(take_screenshot)
            if not isinstance(payload, dict):
                return "[screenshot] Formato de captura inválido"
            b64 = str(payload.get("image_base64") or "").strip()
            if not b64:
                return "[screenshot] No se pudo capturar la pantalla"
            api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
            if not api_key or api_key == "__IN_ENV_SECRETS__":
                return "[screenshot] OPENAI_API_KEY no disponible para visión"
            model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
            async with httpx.AsyncClient(timeout=20.0) as c:
                resp = await c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "max_tokens": 200,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Describe brevemente qué aplicaciones están abiertas y qué está haciendo el usuario. Sé conciso, máx 3 oraciones.",
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
                                    },
                                ],
                            }
                        ],
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                return f"[screenshot] vision API error {resp.status_code}"
        except Exception as exc:
            return f"[screenshot_error] {exc}"

    async def running_apps(self) -> str:
        return await self.bash(
            "osascript -e 'tell application \"System Events\" to get name of every application process whose background only is false'"
        )

    async def type_text(self, text: str) -> str:
        if not text:
            return "[error] Texto vacío"
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return await self.bash(
            f'osascript -e \'tell application "System Events" to keystroke "{escaped}"\''
        )

    async def key_press(self, keys: str) -> str:
        if not keys:
            return "[error] Teclas vacías"
        _key_map = {
            "cmd": "command", "ctrl": "control",
            "alt": "option", "opt": "option", "shift": "shift",
        }
        parts = keys.lower().strip().split("+")
        modifiers = [_key_map.get(p.strip(), p.strip()) for p in parts[:-1]]
        key = parts[-1].strip()
        if modifiers:
            mods = ", ".join(f"{m} down" for m in modifiers)
            script = f'tell application "System Events" to keystroke "{key}" using {{{mods}}}'
        else:
            script = f'tell application "System Events" to keystroke "{key}"'
        return await self.bash(f"osascript -e '{script}'")

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Dispatch a named tool call."""
        name = str(tool_name or "").strip().lower()
        try:
            if name == "bash":
                return await self.bash(str(args.get("command") or ""))
            if name == "open_url":
                return await self.open_url(str(args.get("url") or ""))
            if name == "screenshot":
                return await self.screenshot()
            if name == "running_apps":
                return await self.running_apps()
            if name == "type_text":
                return await self.type_text(str(args.get("text") or ""))
            if name == "key_press":
                return await self.key_press(str(args.get("keys") or ""))
            return f"[error] Herramienta '{name}' desconocida. Usa: bash, open_url, screenshot, running_apps, type_text, key_press"
        except Exception as exc:
            return f"[tool_error:{name}] {exc}"


# ── LLM call (directo via httpx) ──────────────────────────────────────────────
async def _call_llm(messages: List[Dict[str, Any]], *, model: Optional[str] = None) -> str:
    """
    Direct OpenAI chat completions call — no session DB, no CognitiveEngine overhead.
    Returns the assistant's text content.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key == "__IN_ENV_SECRETS__":
        raise RuntimeError("OPENAI_API_KEY no configurada")
    m = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": m,
                "max_tokens": 512,
                "temperature": 0.2,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Response parser ───────────────────────────────────────────────────────────
def _parse_llm_response(content: str) -> Dict[str, Any]:
    """
    Parse THOUGHT/TOOL/ARGS or THOUGHT/FINAL from LLM output.
    Returns dict with keys: thought, tool, args, final.
    """
    result: Dict[str, Any] = {"thought": "", "tool": None, "args": {}, "final": None}
    lines = (content or "").strip().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("THOUGHT:"):
            result["thought"] = line[8:].strip()
        elif line.upper().startswith("TOOL:"):
            result["tool"] = line[5:].strip().lower()
        elif line.upper().startswith("ARGS:"):
            raw_args = line[5:].strip()
            # Accumulate multiline JSON
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if any(next_line.upper().startswith(k) for k in ("THOUGHT:", "TOOL:", "FINAL:", "ARGS:")):
                    break
                raw_args += " " + next_line
                j += 1
            try:
                result["args"] = json.loads(raw_args)
            except Exception:
                # Fallback: treat whole thing as bash command
                clean = raw_args.strip().strip('"\'')
                if clean:
                    result["args"] = {"command": clean}
        elif line.upper().startswith("FINAL:"):
            final_text = line[6:].strip()
            # Grab subsequent non-keyword lines
            j = i + 1
            while j < len(lines):
                extra = lines[j].strip()
                if not extra or any(extra.upper().startswith(k) for k in ("THOUGHT:", "TOOL:", "ARGS:")):
                    break
                final_text += " " + extra
                j += 1
            result["final"] = final_text.strip()
            break
        i += 1
    return result


# ── AgentBridge ───────────────────────────────────────────────────────────────
class AgentBridge:
    """
    Motor de razonamiento ReAct de grado superior.

    Reemplaza AutonomousAgent + StrictPlanner + MultiAgentRuntime para casos
    que requieren acción directa en el sistema (abrir apps, ejecutar comandos,
    navegar, etc.).

    Uso:
        bridge = AgentBridge(client_id="...", max_iterations=5)
        result = await bridge.run("abre google chrome")
        # result["status"] == "completed"
        # result["summary"] == "Google Chrome ha sido abierto, señor."
    """

    def __init__(
        self,
        *,
        client_id: str,
        max_iterations: int = 5,
        model: Optional[str] = None,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self.max_iterations = max(1, min(int(max_iterations), 10))
        self.model = model
        self._tools = ToolBox()
        self._steps: List[ReActStep] = []
        self._run_id: str = ""
        self._last_failed_fingerprint: str = ""
        self._consecutive_failures: int = 0
        self._forced_direct_bash: bool = False
        self._debug_steps = str(os.getenv("AGENTBRIDGE_INCLUDE_DEBUG_STEPS", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
        }

    def _build_messages(self, goal: str) -> List[Dict[str, Any]]:
        """Construct the full message array for each ReAct iteration."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]
        if not self._steps:
            messages.append({"role": "user", "content": f"OBJETIVO: {goal}"})
        else:
            # Embed full history so LLM sees what happened
            history_lines = [f"OBJETIVO: {goal}", "", "HISTORIAL:"]
            for step in self._steps:
                history_lines += [
                    f"\n[Iteración {step.iteration}]",
                    f"THOUGHT: {step.thought}",
                    f"TOOL: {step.tool}",
                    f"ARGS: {json.dumps(step.args, ensure_ascii=False)}",
                    f"OBSERVACIÓN: {step.observation[:500]}",
                ]
            history_lines.append("\n¿Cuál es el siguiente paso? Si completaste la tarea, usa FINAL.")
            messages.append({"role": "user", "content": "\n".join(history_lines)})
        return messages

    @staticmethod
    def _observation_ok(observation: str) -> bool:
        probe = str(observation or "").strip().lower()
        return not any(probe.startswith(prefix) for prefix in _FAILURE_PREFIXES)

    @staticmethod
    def _sanitize_summary(raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    for key in ("summary", "result", "message", "content"):
                        candidate = str(payload.get(key) or "").strip()
                        if candidate:
                            text = candidate
                            break
            except Exception:
                pass
        lines: List[str] = []
        for line in text.splitlines():
            probe = str(line or "").strip()
            while True:
                match = _DEBUG_PREFIX_RE.match(probe)
                if not match:
                    break
                probe = str(match.group(3) or "").strip()
            if not probe or _DEBUG_TOKEN_RE.match(probe):
                continue
            clean = probe.strip(" ,;")
            if clean:
                lines.append(clean)
        merged = " ".join(lines)
        merged = re.sub(r"\s{2,}", " ", merged).strip()
        return merged[:320]

    @staticmethod
    def _action_fingerprint(tool_name: str, args: Dict[str, Any]) -> str:
        tool = str(tool_name or "").strip().lower()
        payload = args if isinstance(args, dict) else {}
        try:
            body = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        except Exception:
            body = "{}"
        return f"{tool}:{body}"

    @staticmethod
    def _looks_like_system_goal(goal: str) -> bool:
        probe = str(goal or "").strip().lower()
        return any(token in probe for token in _SYSTEM_TASK_HINTS)

    @staticmethod
    def _extract_app_name(goal: str) -> str:
        text = str(goal or "").strip()
        lower = text.lower()
        for alias, canonical in _APP_ALIASES.items():
            if alias in lower:
                return canonical
        for marker in ("abre ", "abrir ", "open "):
            if marker not in lower:
                continue
            idx = lower.find(marker)
            candidate = text[idx + len(marker):].strip()
            for stop in (" y ", ",", ".", " luego ", " then "):
                pos = candidate.lower().find(stop)
                if pos > 0:
                    candidate = candidate[:pos].strip()
                    break
            candidate = candidate.strip("'\" ")
            if candidate:
                return candidate.title()
        return ""

    def _build_direct_bash_fallback(self, goal: str) -> str:
        app_name = self._extract_app_name(goal)
        lower_goal = str(goal or "").lower()
        if "google" in lower_goal and not app_name:
            return "open 'https://www.google.com'"
        if app_name:
            escaped_app = app_name.replace("'", "'\"'\"'")
            return (
                f"open -a '{escaped_app}' "
                f"|| osascript -e 'tell application \"{app_name}\" to activate'"
            )
        return "osascript -e 'tell application \"System Events\" to get name of every application process whose background only is false'"

    def _maybe_force_direct_bash(
        self,
        *,
        goal: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        if not self._looks_like_system_goal(goal):
            return tool_name, args
        if self._consecutive_failures < 2 and self._action_fingerprint(tool_name, args) != self._last_failed_fingerprint:
            return tool_name, args
        command = self._build_direct_bash_fallback(goal)
        if not command:
            return tool_name, args
        self._forced_direct_bash = True
        return "bash", {"command": command}

    @staticmethod
    def _looks_like_web_goal(goal: str) -> bool:
        probe = str(goal or "").strip().lower()
        if not probe:
            return False
        if _WEB_URL_RE.search(probe):
            return True
        return any(token in probe for token in _WEB_TASK_HINTS)

    async def _run_web_agent(self, goal: str) -> Dict[str, Any]:
        from browser_agent.web_agent import WebAgent

        max_steps = max(3, min(int(str(os.getenv("WEB_AGENT_MAX_STEPS") or "15")), 30))
        timeout_s = max(2.0, float(str(os.getenv("WEB_AGENT_ACTION_TIMEOUT_SEC") or "12")))
        web_agent = WebAgent(
            max_steps=max_steps,
            action_timeout_s=timeout_s,
            model=self.model,
        )
        result = await web_agent.run(goal)
        success = bool(result.get("success", False))
        summary = self._sanitize_summary(str(result.get("summary") or ""))
        if not summary:
            summary = "Tarea web completada." if success else "No pude completar la tarea web."

        response: Dict[str, Any] = {
            "status": "completed" if success else "failed",
            "goal": str(goal or "").strip(),
            "summary": summary,
            "iterations": len(list(result.get("actions") or [])),
            "runtime": "web_agent_loop",
            "run_id": self._run_id,
            "data": result.get("data") if isinstance(result.get("data"), dict) else {},
        }
        if self._debug_steps:
            response["steps"] = list(result.get("actions") or [])
            response["pages_visited"] = list(result.get("pages_visited") or [])
            response["results"] = list(result.get("results") or [])
        else:
            response["steps"] = []
        return response

    async def run(self, goal: str) -> Dict[str, Any]:
        """
        Delegate all runtime reasoning/execution to MasterBrain.
        Returns the legacy AgentBridge contract shape.
        """
        self._steps = []
        self._run_id = str(uuid4())[:8]
        self._last_failed_fingerprint = ""
        self._consecutive_failures = 0
        self._forced_direct_bash = False
        goal_text = str(goal or "").strip()

        logger.info("[AgentBridge:%s] goal=%r max_iter=%d", self._run_id, goal_text[:80], self.max_iterations)

        if not goal_text:
            return {
                "status": "failed",
                "goal": goal_text,
                "summary": "No recibí un objetivo válido.",
                "iterations": 0,
                "runtime": "master_brain",
                "run_id": self._run_id,
                "steps": [],
                "data": {},
            }

        if str(os.getenv("ENABLE_MASTER_BRAIN", "true")).strip().lower() not in {"1", "true", "yes"}:
            logger.warning(
                "[AgentBridge:%s] ENABLE_MASTER_BRAIN está deprecado y se ignora; MasterBrain seguirá activo.",
                self._run_id,
            )
        if os.getenv("MASTER_BRAIN_FALLBACK_REACT") is not None:
            logger.warning(
                "[AgentBridge:%s] MASTER_BRAIN_FALLBACK_REACT está deprecado y sin efecto.",
                self._run_id,
            )

        try:
            from brain.master_brain import MasterBrain

            master = MasterBrain(
                client_id=self.client_id,
                model=self.model,
                max_steps=int(str(os.getenv("MASTER_BRAIN_MAX_STEPS") or self.max_iterations)),
                run_id=self._run_id,
            )
            _timeout_sec = _bridge_timeout_seconds(goal_text)
            try:
                master_result = await asyncio.wait_for(
                    master.run(goal_text), timeout=float(_timeout_sec)
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[AgentBridge:%s] MasterBrain timed out after %ds run_id=%s",
                    self._run_id,
                    _timeout_sec,
                    self._run_id,
                )
                return {
                    "status": "failed",
                    "goal": goal_text,
                    "summary": (
                        f"La tarea tardó más de {_timeout_sec}s. "
                        "Intenta con un objetivo más específico."
                    ),
                    "iterations": 0,
                    "runtime": "master_brain",
                    "run_id": self._run_id,
                    "steps": [],
                    "data": {"timeout_sec": _timeout_sec},
                }
            status = str(master_result.get("status") or "failed").strip().lower() or "failed"
            summary = self._sanitize_summary(str(master_result.get("summary") or ""))
            if not summary:
                summary = "Tarea completada." if status == "completed" else "No pude completar la tarea."
            raw_data = master_result.get("data")
            normalized_data: Dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            if isinstance(normalized_data.get("data"), dict):
                # Preserve legacy AgentBridge contract: direct execution payload at top level.
                normalized_data = dict(normalized_data.get("data") or {})
            response: Dict[str, Any] = {
                "status": status,
                "goal": goal_text,
                "summary": summary,
                "iterations": int(master_result.get("iterations") or 0),
                "runtime": str(master_result.get("runtime") or "master_brain"),
                "run_id": str(master_result.get("run_id") or self._run_id),
                "data": normalized_data,
            }
            if self._debug_steps:
                response["steps"] = list(master_result.get("steps") or [])
                response["needs_confirmation"] = bool(master_result.get("needs_confirmation", False))
                response["confirmation_reason"] = str(
                    master_result.get("confirmation_reason") or ""
                ).strip()
            else:
                response["steps"] = []
            return response
        except Exception as exc:
            logger.exception("[AgentBridge:%s] MasterBrain failed: %s", self._run_id, exc)
            return {
                "status": "failed",
                "goal": goal_text,
                "summary": self._sanitize_summary(f"MasterBrain unavailable: {exc}") or "MasterBrain unavailable.",
                "iterations": 0,
                "runtime": "master_brain",
                "run_id": self._run_id,
                "steps": [],
                "data": {},
            }

    def cancel(self) -> None:
        """No-op for API compatibility — cancellation handled via asyncio.Task."""
        pass
