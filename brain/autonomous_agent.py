"""
Tu Autonomía — Agente autónomo propio.
Controla la computadora como un humano, descubre APIs y usa runtime unificado.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

# ── Quick-bash short-circuit ──────────────────────────────────────────────────
# Maps common spoken names → macOS app names.
# Before going through the full planner, detect open/close commands and execute
# them directly via subprocess — one OS call, no vision, no multi-agent loop.
_QUICK_APPS: Dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "safari": "Safari",
    "firefox": "Firefox",
    "terminal": "Terminal",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "slack": "Slack",
    "spotify": "Spotify",
    "zoom": "Zoom",
    "finder": "Finder",
    "notes": "Notes",
    "calendar": "Calendar",
    "mail": "Mail",
    "messages": "Messages",
    "facetime": "FaceTime",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "discord": "Discord",
    "notion": "Notion",
    "figma": "Figma",
    "xcode": "Xcode",
    "preview": "Preview",
    "photos": "Photos",
    "music": "Music",
    "podcasts": "Podcasts",
    "maps": "Maps",
    "calculator": "Calculator",
}

_OPEN_TRIGGERS = (
    "abre ", "abre el ", "abre la ", "abre los ", "abre las ",
    "open ", "inicia ", "inicia el ", "lanza ", "lanza el ",
    "abre la app ", "abre la aplicacion ",
)
_CLOSE_TRIGGERS = (
    "cierra ", "cierra el ", "cierra la ",
    "close ", "quit ", "mata ",
)


def _resolve_app(raw: str) -> Optional[str]:
    """Return canonical macOS app name or None if unknown."""
    clean = raw.strip().lower().rstrip(".,!? ")
    if clean in _QUICK_APPS:
        return _QUICK_APPS[clean]
    for key, val in _QUICK_APPS.items():
        if key in clean:
            return val
    return None

from brain.api_discovery_engine import APIDiscoveryEngine
from brain.core import CognitiveEngine
from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
from brain.multi_agent_runtime import run_multi_agent_handoff
from brain.strict_planner import StrictPlanner
from brain.system_permissions import SystemPermissionsManager
from browser_agent.browser_controller import BrowserController
from database import AsyncSessionLocal
from desktop_agent.controller import DesktopController
from schemas.desktop_autonomy import DesktopAutonomyRunRequest
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("kan_core.autonomy")


class AutonomousAgent:
    """
    Runtime único de autonomía:
    - planner estricto con schema + reintentos
    - ejecución central en desktop_autonomous_loop
    - handoff multi-agente planner->specialists->critic->executor
    """

    def __init__(
        self,
        *,
        client_id: str,
        agent_id: Optional[str] = None,
        execution_mode: str = "autonomous",
        enable_api_discovery: bool = True,
        enable_browser: bool = True,
        enable_desktop_control: bool = True,
        max_concurrent_tasks: int = 3,
    ):
        self.client_id = client_id
        self.agent_id = agent_id or str(uuid4())
        self.execution_mode = execution_mode
        self.enable_api_discovery = enable_api_discovery
        self.enable_browser = enable_browser
        self.enable_desktop_control = enable_desktop_control
        self.max_concurrent_tasks = max_concurrent_tasks

        self._cognitive_engine: Optional[CognitiveEngine] = None
        self._browser: Optional[BrowserController] = None
        self._desktop_controller: Optional[DesktopController] = None
        self._session: Optional[AsyncSession] = None
        self._planner = StrictPlanner(max_retries=3)
        self._running = False
        self._discovered_apis: List[Dict[str, Any]] = []

    async def initialize(self) -> None:
        logger.info("Inicializando Tu Autonomía...")

        self._session = AsyncSessionLocal()
        self._cognitive_engine = CognitiveEngine.get_instance()
        logger.info("✓ Inteligencia (GPT) lista")

        if self.enable_browser:
            try:
                self._browser = BrowserController(
                    enabled=True,
                    headless=False,
                    channel="chromium",
                    user_data_dir="",
                    domain_allowlist=[],
                    allow_all_domains=True,
                    default_timeout_ms=15000,
                )
                logger.info("✓ Navegador listo")
            except Exception as e:
                logger.warning("Navegador no disponible: %s", e)

        if self.enable_desktop_control:
            try:
                self._desktop_controller = DesktopController(
                    open_program_allowlist={},
                    unsafe_allow_all=True,
                    browser_enabled=False,
                )
                logger.info("✓ Control del escritorio listo")
            except Exception as e:
                logger.warning("Control de escritorio no disponible: %s", e)

        await self._check_system_permissions()
        logger.info("✓ Tu Autonomía está lista")

    async def _check_system_permissions(self) -> None:
        manager = SystemPermissionsManager()
        results = await asyncio.to_thread(manager.check_all_permissions)
        if not all(results.values()):
            logger.warning("⚠️  Faltan algunos permisos del sistema")
            await asyncio.to_thread(manager.print_permission_instructions)
        else:
            logger.info("✓ Permisos OK")

    async def think(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._cognitive_engine:
            raise RuntimeError("Inteligencia no inicializada")
        return await self._planner.build_plan(
            engine=self._cognitive_engine,
            client_id=self.client_id,
            session_id=f"autonomy_{self.agent_id}",
            goal=goal,
            context=context,
        )

    async def discover_apis(
        self,
        source: str,
        *,
        url: Optional[str] = None,
        browser_url: Optional[str] = None,
        terminal_command: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("Sesión no inicializada")
        engine = APIDiscoveryEngine(self._session, self.client_id)
        try:
            if source == "url" and url:
                result = await engine.discover_from_url(url, auto_encrypt=True)
            elif source == "browser" and browser_url:
                if not self._browser:
                    raise RuntimeError("Navegador no inicializado")
                result = await engine.discover_from_browser(
                    self._browser, target_url=browser_url, auto_encrypt=True
                )
            elif source == "terminal" and terminal_command:
                result = await engine.discover_from_terminal(
                    terminal_command, auto_encrypt=True
                )
            else:
                raise ValueError(f"Fuente inválida: {source}")
            self._discovered_apis.extend(result.get("discovered_apis", []))
            return result
        finally:
            await engine.close()

    async def _try_quick_bash(self, goal: str) -> Optional[Dict[str, Any]]:
        """
        Short-circuit: detect simple open/close app commands and execute them
        directly via macOS `open -a` / AppleScript quit, bypassing the full planner.
        Returns a result dict if handled, None if not applicable.
        """
        probe = str(goal or "").strip().lower()

        # open -a '<App>'
        for trigger in _OPEN_TRIGGERS:
            if probe.startswith(trigger):
                raw = probe[len(trigger):]
                app = _resolve_app(raw)
                if app:
                    cmd = f"open -a '{app}'"
                    try:
                        proc = await asyncio.create_subprocess_shell(
                            cmd,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(proc.communicate(), timeout=5.0)
                        if proc.returncode == 0:
                            logger.info("[quick_bash] %s → ok", cmd)
                            return {
                                "goal": goal,
                                "status": "completed",
                                "summary": f"{app} ha sido abierto.",
                                "iterations": 1,
                                "runtime": "quick_bash",
                            }
                    except Exception as exc:
                        logger.debug("[quick_bash] failed: %s", exc)
                break

        # osascript quit '<App>'
        for trigger in _CLOSE_TRIGGERS:
            if probe.startswith(trigger):
                raw = probe[len(trigger):]
                app = _resolve_app(raw)
                if app:
                    cmd = f"osascript -e 'tell application \"{app}\" to quit'"
                    try:
                        proc = await asyncio.create_subprocess_shell(
                            cmd,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(proc.communicate(), timeout=5.0)
                        logger.info("[quick_bash] %s → ok", cmd)
                        return {
                            "goal": goal,
                            "status": "completed",
                            "summary": f"{app} ha sido cerrado.",
                            "iterations": 1,
                            "runtime": "quick_bash",
                        }
                    except Exception as exc:
                        logger.debug("[quick_bash] failed: %s", exc)
                break

        return None

    async def run_autonomous_loop(
        self,
        goal: str,
        *,
        max_iterations: int = 10,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("Sesión no inicializada")

        self._running = True
        runtime_results: List[Dict[str, Any]] = []
        ctx = dict(context or {})
        max_steps = max(1, min(int(max_iterations), 30))

        logger.info("Objetivo: %s", goal)

        try:
            # ── Short-circuit: simple open/close bash commands ─────────────
            quick = await self._try_quick_bash(goal)
            if quick:
                return quick

            thinking = await self.think(goal, ctx)
            plan = thinking.get("plan") if isinstance(thinking.get("plan"), dict) else {}
            actions = list(plan.get("actions") or [])
            runtime_context = self._context_from_plan(ctx, actions)

            if self.enable_api_discovery:
                for item in actions:
                    if str(item.get("type") or "") != "discover_apis":
                        continue
                    discovery = await self.discover_apis(
                        source=str(item.get("source") or "url"),
                        url=item.get("url"),
                        browser_url=item.get("browser_url"),
                        terminal_command=item.get("command"),
                    )
                    runtime_results.append({"action": "discover_apis", "result": discovery})

            use_multi_agent = os.getenv("ENABLE_MULTI_AGENT_RUNTIME", "true").lower() in {
                "1",
                "true",
                "yes",
            }
            if use_multi_agent:
                handoff = await run_multi_agent_handoff(
                    client_id=self.client_id,
                    goal=goal,
                    context=runtime_context,
                    max_steps=max_steps,
                    session=self._session,
                )
                runtime_results.append({"action": "multi_agent_handoff", "result": handoff})
                execution = handoff.get("execution") if isinstance(handoff, dict) else {}
                status = str((execution or {}).get("status") or "failed")
                return {
                    "goal": goal,
                    "iterations": 1,
                    "results": runtime_results,
                    "discovered_apis": len(self._discovered_apis),
                    "status": status,
                    "runtime": "desktop_autonomous_loop",
                    "planner": "strict",
                }

            request = DesktopAutonomyRunRequest(
                goal=goal,
                max_steps=max_steps,
                execution_mode=self.execution_mode
                if self.execution_mode in {"safe", "balanced", "autonomous"}
                else "autonomous",
                context=runtime_context,
                dry_run=bool(ctx.get("dry_run", False)),
                wait_for_results=True,
                enable_recovery=True,
                enable_self_healing=True,
                enforce_quality_gate=False,
                dynamic_risk_controls=False,
            )
            response = await run_desktop_autonomy_loop(
                client_id=self.client_id,
                request=request,
                session=self._session,
            )
            runtime_results.append({"action": "desktop_autonomy", "result": response.model_dump()})
            return {
                "goal": goal,
                "iterations": 1,
                "results": runtime_results,
                "discovered_apis": len(self._discovered_apis),
                "status": response.status,
                "runtime": "desktop_autonomous_loop",
                "planner": "strict",
            }
        finally:
            self._running = False

    def _context_from_plan(
        self,
        base_context: Dict[str, Any],
        actions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Translate strict planner actions into execution hints for desktop_autonomy."""
        merged = dict(base_context or {})
        fill_steps: List[Dict[str, Any]] = list(merged.get("fill_steps") or [])
        menu_path: List[str] = list(merged.get("menu_path") or [])
        click_text: Optional[str] = merged.get("click_text") if isinstance(merged.get("click_text"), str) else None
        click_selector: Optional[str] = (
            merged.get("click_selector") if isinstance(merged.get("click_selector"), str) else None
        )
        extract_selector: Optional[str] = (
            merged.get("extract_selector") if isinstance(merged.get("extract_selector"), str) else None
        )
        max_wait = float(merged.get("planner_wait_seconds") or 0.0)
        start_url = merged.get("start_url") if isinstance(merged.get("start_url"), str) else None

        for item in actions:
            action_type = str(item.get("type") or "")
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            raw_action = str(item.get("action") or "")
            if action_type == "browser":
                if raw_action in {"goto", "browser_goto"}:
                    url = item.get("url") or params.get("url")
                    if isinstance(url, str) and url.strip():
                        start_url = url.strip()
                elif raw_action in {"click", "browser_click"}:
                    if isinstance(params.get("text"), str) and params.get("text").strip():
                        click_text = str(params.get("text")).strip()
                        menu_path.append(click_text)
                    if isinstance(params.get("selector"), str) and params.get("selector").strip():
                        click_selector = str(params.get("selector")).strip()
                elif raw_action in {"fill", "browser_fill"}:
                    selector = params.get("selector")
                    text = params.get("text")
                    if isinstance(selector, str) and isinstance(text, str):
                        fill_steps.append({"selector": selector, "text": text})
                elif raw_action in {"extract", "browser_extract"}:
                    selector = params.get("selector")
                    if isinstance(selector, str) and selector.strip():
                        extract_selector = selector.strip()
            elif action_type == "wait":
                seconds = item.get("seconds")
                try:
                    max_wait = max(max_wait, float(seconds or 0.0))
                except Exception:
                    pass

        if isinstance(start_url, str) and start_url.strip():
            merged["start_url"] = start_url.strip()
        if fill_steps:
            merged["fill_steps"] = fill_steps[:20]
        if menu_path:
            deduped: List[str] = []
            seen = set()
            for token in menu_path:
                value = str(token or "").strip()
                if not value:
                    continue
                key = value.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(value)
            merged["menu_path"] = deduped[:10]
        if click_text:
            merged["click_text"] = click_text
        if click_selector:
            merged["click_selector"] = click_selector
        if extract_selector:
            merged["extract_selector"] = extract_selector
        if max_wait > 0:
            merged["planner_wait_seconds"] = round(max_wait, 3)
        return merged

    async def stop(self) -> None:
        self._running = False
        if self._browser:
            try:
                await self._browser.browser_close()
            except Exception:
                pass
        if self._session:
            await self._session.close()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
