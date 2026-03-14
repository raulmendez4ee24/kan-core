import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any, Dict

from browser_agent.browser_controller import BrowserController
from desktop_agent.actions import click, mover_mouse, open_program, press_key, type_text
from desktop_agent.screen_capture import take_screenshot


class DesktopController:
    def __init__(
        self,
        *,
        open_program_allowlist: Dict[str, str],
        screenshot_max_width: int = 1600,
        unsafe_allow_all: bool = False,
        browser_enabled: bool = True,
        browser_headless: bool = False,
        browser_channel: str = "chromium",
        browser_user_data_dir: str = "",
        browser_domain_allowlist: list[str] | None = None,
        browser_default_timeout_ms: int = 15000,
        browser_max_attempts: int = 2,
    ) -> None:
        self._allowlist = {str(k).strip().lower(): str(v).strip() for k, v in open_program_allowlist.items()}
        self._screenshot_max_width = screenshot_max_width
        self._unsafe_allow_all = bool(unsafe_allow_all)
        # Executor only used for PyAutoGUI (non-browser) actions.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-controller")
        self._browser_max_attempts = max(1, min(int(browser_max_attempts), 5))
        self._screen_capture_lock = asyncio.Lock()
        self._browser = BrowserController(
            enabled=browser_enabled,
            headless=browser_headless,
            channel=browser_channel,
            user_data_dir=browser_user_data_dir,
            domain_allowlist=list(browser_domain_allowlist or []),
            allow_all_domains=self._unsafe_allow_all,
            default_timeout_ms=browser_default_timeout_ms,
        )

    @staticmethod
    def _is_browser_action(action: str) -> bool:
        return str(action or "").startswith("browser_")

    @staticmethod
    def _is_transient_browser_error(error: str) -> bool:
        token = str(error or "").strip().lower()
        if not token:
            return False
        hints = (
            "execution_timeout",
            "timeout",
            "target closed",
            "has been closed",
            "connection closed",
            "socket",
            "net::",
            "page crashed",
            "context closed",
            "no close frame",
            "internal error",
        )
        return any(h in token for h in hints)

    def _validate_args(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(args or {})

        if action == "mover_mouse":
            if "x" not in payload or "y" not in payload:
                raise ValueError("mover_mouse requires x and y")
            return {"x": int(payload["x"]), "y": int(payload["y"])}

        if action == "click":
            click_payload = {
                "button": str(payload.get("button", "left")),
                "clicks": int(payload.get("clicks", 1)),
            }
            if "x" in payload or "y" in payload:
                if "x" not in payload or "y" not in payload:
                    raise ValueError("click requires both x and y when coordinates are used")
                click_payload["x"] = int(payload["x"])
                click_payload["y"] = int(payload["y"])
            return click_payload

        if action == "type_text":
            if "text" not in payload:
                raise ValueError("type_text requires text")
            return {
                "text": str(payload["text"]),
                "interval": float(payload.get("interval", 0.0)),
            }

        if action == "press_key":
            if "key" not in payload:
                raise ValueError("press_key requires key")
            return {"key": str(payload["key"])}

        if action == "open_program":
            if "name" not in payload:
                raise ValueError("open_program requires name")
            return {
                "name": str(payload["name"]),
                "allowlist": self._allowlist,
                "unsafe_allow_any": self._unsafe_allow_all,
            }

        if action == "take_screenshot":
            max_width = payload.get("max_width", self._screenshot_max_width)
            return {"max_width": int(max_width)}

        if action == "browser_launch":
            launch_payload: Dict[str, Any] = {}
            if "headless" in payload:
                launch_payload["headless"] = bool(payload["headless"])
            if "channel" in payload:
                launch_payload["channel"] = str(payload["channel"])
            if "start_url" in payload:
                launch_payload["start_url"] = str(payload["start_url"])
            return launch_payload

        if action == "browser_goto":
            if "url" not in payload:
                raise ValueError("browser_goto requires url")
            return {
                "url": str(payload["url"]),
                "wait_until": str(payload.get("wait_until", "domcontentloaded")),
            }

        if action == "browser_click":
            click_payload: Dict[str, Any] = {}
            has_target = False
            for key in ("selector", "text", "role", "name"):
                if key in payload and payload[key] is not None:
                    click_payload[key] = str(payload[key])
            if any(key in click_payload for key in ("selector", "text", "role")):
                has_target = True
            has_coords = False
            if "x" in payload or "y" in payload:
                if "x" not in payload or "y" not in payload:
                    raise ValueError("browser_click requires both x and y when coordinates are used")
                click_payload["x"] = int(payload["x"])
                click_payload["y"] = int(payload["y"])
                has_coords = True
            if "timeout_ms" in payload and payload["timeout_ms"] is not None:
                click_payload["timeout_ms"] = int(payload["timeout_ms"])
            if not has_target and not has_coords:
                raise ValueError("browser_click requires selector, text, role, or x/y")
            return click_payload

        if action == "browser_fill":
            if "selector" not in payload or "text" not in payload:
                raise ValueError("browser_fill requires selector and text")
            fill_payload: Dict[str, Any] = {
                "selector": str(payload["selector"]),
                "text": str(payload["text"]),
                "clear_first": bool(payload.get("clear_first", True)),
            }
            if "timeout_ms" in payload and payload["timeout_ms"] is not None:
                fill_payload["timeout_ms"] = int(payload["timeout_ms"])
            return fill_payload

        if action == "browser_press":
            if "selector" not in payload or "key" not in payload:
                raise ValueError("browser_press requires selector and key")
            press_payload: Dict[str, Any] = {
                "selector": str(payload["selector"]),
                "key": str(payload["key"]),
            }
            if "timeout_ms" in payload and payload["timeout_ms"] is not None:
                press_payload["timeout_ms"] = int(payload["timeout_ms"])
            return press_payload

        if action == "browser_extract":
            if "selector" not in payload:
                raise ValueError("browser_extract requires selector")
            extract_payload: Dict[str, Any] = {
                "selector": str(payload["selector"]),
                "all_matches": bool(payload.get("all_matches", False)),
            }
            if "attribute" in payload and payload["attribute"] is not None:
                extract_payload["attribute"] = str(payload["attribute"])
            if "timeout_ms" in payload and payload["timeout_ms"] is not None:
                extract_payload["timeout_ms"] = int(payload["timeout_ms"])
            return extract_payload

        if action == "browser_screenshot":
            return {"full_page": bool(payload.get("full_page", False))}

        if action == "browser_close":
            return {}

        raise ValueError(f"Unsupported action: {action}")

    def _run_desktop_action(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run PyAutoGUI / OS-level actions synchronously in a thread executor."""
        if action == "mover_mouse":
            return mover_mouse(**args)
        if action == "click":
            return click(**args)
        if action == "type_text":
            return type_text(**args)
        if action == "press_key":
            return press_key(**args)
        if action == "open_program":
            return open_program(**args)
        if action == "take_screenshot":
            return take_screenshot(**args)
        raise ValueError(f"Unsupported desktop action: {action}")

    async def _run_browser_action(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run browser actions using async Playwright directly (no executor needed)."""
        if action == "browser_launch":
            return await self._browser.browser_launch(**args)
        if action == "browser_goto":
            return await self._browser.browser_goto(**args)
        if action == "browser_click":
            return await self._browser.browser_click(**args)
        if action == "browser_fill":
            return await self._browser.browser_fill(**args)
        if action == "browser_press":
            return await self._browser.browser_press(**args)
        if action == "browser_extract":
            return await self._browser.browser_extract(**args)
        if action == "browser_screenshot":
            return await self._browser.browser_screenshot(**args)
        if action == "browser_close":
            return await self._browser.browser_close()
        raise ValueError(f"Unsupported browser action: {action}")

    async def execute(
        self,
        *,
        action: str,
        args: Dict[str, Any],
        timeout_ms: int,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            validated = self._validate_args(action, args)
            timeout_seconds = max(float(timeout_ms) / 1000.0, 0.2)
            attempts = self._browser_max_attempts if self._is_browser_action(action) else 1
            last_error = ""

            for attempt in range(1, attempts + 1):
                try:
                    if self._is_browser_action(action):
                        # Browser actions use async Playwright directly — no thread executor.
                        payload = await asyncio.wait_for(
                            self._run_browser_action(action, validated),
                            timeout=timeout_seconds,
                        )
                    else:
                        # PyAutoGUI / OS actions run in the thread executor.
                        loop = asyncio.get_running_loop()
                        if action == "take_screenshot":
                            async with self._screen_capture_lock:
                                payload = await asyncio.wait_for(
                                    loop.run_in_executor(
                                        self._executor, self._run_desktop_action, action, validated
                                    ),
                                    timeout=timeout_seconds,
                                )
                        else:
                            payload = await asyncio.wait_for(
                                loop.run_in_executor(
                                    self._executor, self._run_desktop_action, action, validated
                                ),
                                timeout=timeout_seconds,
                            )
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    return {
                        "status": "success",
                        "error": None,
                        "duration_ms": duration_ms,
                        "payload": payload,
                    }
                except asyncio.TimeoutError:
                    last_error = "execution_timeout"
                except Exception as exc:
                    last_error = str(exc)

                if attempt >= attempts:
                    break
                if not self._is_browser_action(action):
                    break
                if not self._is_transient_browser_error(last_error):
                    break
                # Browser fallback: recycle Playwright context and retry.
                try:
                    await asyncio.wait_for(
                        self._browser.browser_close(),
                        timeout=min(timeout_seconds, 10.0),
                    )
                except Exception:
                    pass
                # small jitter before retry helps flaky page states
                await asyncio.sleep(min(0.25 * attempt, 0.75))

            duration_ms = int((time.perf_counter() - started) * 1000)
            return {
                "status": "failed",
                "error": last_error or "execution_failed",
                "duration_ms": duration_ms,
                "payload": {},
            }
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return {
                "status": "failed",
                "error": str(exc),
                "duration_ms": duration_ms,
                "payload": {},
            }
