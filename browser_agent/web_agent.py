from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from browser_agent.browser_controller import BrowserController

from config.env_helpers import env_bool

logger = logging.getLogger("kan_core.web_agent")

_URL_RE = re.compile(r"(https?://[^\s)]+|www\.[^\s)]+)", re.IGNORECASE)
_DONE_ACTIONS = {"done", "finish", "stop"}
_VISION_PROMPT = (
    "Describe briefly what is visible and what user should click/type next to complete the task."
)


def _truncate_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _extract_first_url(text: str) -> str:
    match = _URL_RE.search(str(text or ""))
    if not match:
        return ""
    url = str(match.group(1) or "").strip().rstrip(".,;")
    if url.lower().startswith("www."):
        return "https://" + url
    return url


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_action_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    selector = str(payload.get("selector") or "").strip()
    text = str(payload.get("text") or "").strip()
    key = str(payload.get("key") or "").strip()
    url = str(payload.get("url") or "").strip()
    reasoning = str(payload.get("reasoning") or "").strip()
    attribute = str(payload.get("attribute") or "").strip()
    all_matches = bool(payload.get("all_matches", False))
    amount = int(payload.get("amount", 850)) if str(payload.get("amount", "")).strip() else 850

    normalized = {
        "action": action,
        "selector": selector,
        "text": text,
        "key": key,
        "url": url,
        "attribute": attribute,
        "all_matches": all_matches,
        "amount": max(100, min(3000, amount)),
        "reasoning": reasoning or "next step",
    }
    return normalized


async def _call_llm_json(
    *,
    messages: List[Dict[str, Any]],
    model: str,
    timeout_s: float,
) -> Dict[str, Any]:
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {}
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0.2,
                "max_tokens": 350,
                "messages": messages,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = (
            resp.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    return _safe_json_loads(str(content))


class WebAgent:
    def __init__(
        self,
        *,
        browser: Optional[BrowserController] = None,
        max_steps: int = 15,
        action_timeout_s: float = 12.0,
        llm_caller: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        model: Optional[str] = None,
    ) -> None:
        self.max_steps = max(1, min(int(max_steps), 30))
        self.action_timeout_s = max(2.0, float(action_timeout_s))
        self.model = str(
            model
            or os.getenv("WEB_AGENT_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4o-mini"
        ).strip()
        self._llm_timeout_s = max(4.0, float(os.getenv("WEB_AGENT_LLM_TIMEOUT", "18")))
        self._llm_caller = llm_caller or _call_llm_json
        self._goal = ""
        self._started = False

        self.pages_visited: List[str] = []
        self.actions_done: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []
        self.extracted_data: Dict[str, Any] = {}
        self._repeated_fingerprint_count = 0
        self._last_fingerprint = ""

        self._browser = browser or self._build_default_browser()

    @staticmethod
    def _build_default_browser() -> BrowserController:
        raw_domains = str(os.getenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON") or "[]")
        domains: List[str] = []
        try:
            parsed = json.loads(raw_domains)
            if isinstance(parsed, list):
                for item in parsed:
                    token = str(item).strip().lower()
                    if token and token not in domains:
                        domains.append(token)
        except Exception:
            domains = []

        allow_all_domains = env_bool("WEB_AGENT_ALLOW_ALL_DOMAINS", "true") or env_bool(
            "DESKTOP_AGENT_UNSAFE_ALLOW_ALL", "false"
        )
        return BrowserController(
            enabled=env_bool("DESKTOP_AGENT_BROWSER_ENABLED", "true"),
            headless=env_bool("DESKTOP_AGENT_BROWSER_HEADLESS", "false"),
            channel=str(os.getenv("DESKTOP_AGENT_BROWSER_CHANNEL") or "chromium").strip().lower(),
            user_data_dir=str(os.getenv("DESKTOP_AGENT_BROWSER_USER_DATA_DIR") or "").strip(),
            domain_allowlist=domains,
            allow_all_domains=allow_all_domains,
            default_timeout_ms=int(str(os.getenv("DESKTOP_AGENT_BROWSER_DEFAULT_TIMEOUT_MS") or "15000")),
        )

    async def open(self, url: str) -> Dict[str, Any]:
        target = str(url or "").strip()
        if not target:
            raise ValueError("open(url) requires a non-empty URL")
        if not self._started:
            result = await self._browser.browser_launch(start_url=target)
            self._started = True
        else:
            result = await self._browser.browser_goto(url=target)
        current_url = str(result.get("url") or target).strip()
        if current_url and current_url not in self.pages_visited:
            self.pages_visited.append(current_url)
        return result

    async def _ensure_page(self) -> Any:
        ensure_session = getattr(self._browser, "_ensure_session", None)
        if callable(ensure_session):
            await ensure_session()
        page = getattr(self._browser, "_page", None)
        if page is None:
            raise RuntimeError("web_agent page not available")
        return page

    async def _observe_with_vision(self, screenshot_b64: str, *, mime_type: str) -> str:
        api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key or not screenshot_b64:
            return ""
        model = str(os.getenv("WEB_AGENT_VISION_MODEL") or "gpt-4o-mini").strip()
        try:
            async with httpx.AsyncClient(timeout=14.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "max_tokens": 200,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": _VISION_PROMPT},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{screenshot_b64}",
                                            "detail": "low",
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                )
                if resp.status_code != 200:
                    return ""
                content = (
                    resp.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return _truncate_text(str(content or ""), 500)
        except Exception:
            return ""

    async def observe(self) -> Dict[str, Any]:
        page = await self._ensure_page()
        title = str(await page.title() or "").strip()
        url = str(getattr(page, "url", "") or "").strip()
        if url and url not in self.pages_visited:
            self.pages_visited.append(url)

        dom_text = str(
            await page.evaluate(
                "() => (document.body && document.body.innerText ? document.body.innerText.slice(0, 2000) : '')"
            )
            or ""
        ).strip()
        elements = await page.evaluate(
            """
            () => {
              const clean = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
              const candidates = Array.from(
                document.querySelectorAll('a,button,input,textarea,select,[role=\"button\"],[onclick],summary')
              );
              const out = [];
              for (let i = 0; i < candidates.length && out.length < 80; i++) {
                const el = candidates[i];
                const tag = (el.tagName || '').toLowerCase();
                const id = clean(el.id);
                const name = clean(el.getAttribute('name'));
                const type = clean(el.getAttribute('type'));
                const role = clean(el.getAttribute('role'));
                const text = clean(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('aria-label'));
                let selector = '';
                if (id) selector = `#${id.replace(/\"/g, '')}`;
                else if (name) selector = `${tag}[name=\"${name.replace(/\"/g, '')}\"]`;
                else if (text) selector = `${tag}:has-text(\"${text.slice(0, 40).replace(/\"/g, '')}\")`;
                else selector = tag;
                out.push({
                  tag,
                  id,
                  name,
                  type,
                  role,
                  text: text.slice(0, 120),
                  selector,
                });
              }
              return out;
            }
            """
        )
        interactive_elements = list(elements or []) if isinstance(elements, list) else []

        screenshot = {}
        include_shot = env_bool("WEB_AGENT_OBSERVE_SCREENSHOT", "false") or len(interactive_elements) <= 3
        if include_shot:
            try:
                screenshot = await self._browser.browser_screenshot(full_page=False)
            except Exception:
                screenshot = {}

        vision_summary = ""
        screenshot_b64 = str((screenshot or {}).get("image_base64") or "").strip()
        mime_type = str((screenshot or {}).get("mime_type") or "image/png").strip() or "image/png"
        if screenshot_b64 and (not dom_text or len(interactive_elements) <= 2):
            vision_summary = await self._observe_with_vision(screenshot_b64, mime_type=mime_type)

        return {
            "url": url,
            "title": title,
            "dom_excerpt": _truncate_text(dom_text, 1800),
            "interactive_elements": interactive_elements[:50],
            "vision_summary": vision_summary,
            "step_count": len(self.actions_done),
        }

    async def decide(self, goal: str, state: Dict[str, Any]) -> Dict[str, Any]:
        state_summary = {
            "url": state.get("url"),
            "title": state.get("title"),
            "dom_excerpt": _truncate_text(str(state.get("dom_excerpt") or ""), 900),
            "vision_summary": _truncate_text(str(state.get("vision_summary") or ""), 400),
            "interactive_elements": list(state.get("interactive_elements") or [])[:25],
            "recent_actions": self.actions_done[-5:],
            "recent_results": self.results[-5:],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a web automation planner. Reply only JSON with keys: "
                    "action, selector, text, key, url, attribute, all_matches, amount, reasoning. "
                    "Allowed actions: goto, click, fill, press, scroll, extract, back, done."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "state": state_summary,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        payload: Dict[str, Any] = {}
        try:
            payload = await self._llm_caller(
                messages=messages,
                model=self.model,
                timeout_s=self._llm_timeout_s,
            )
        except Exception as exc:
            logger.warning("web_agent decide LLM failed: %s", exc)

        normalized = _normalize_action_payload(payload)
        if normalized["action"]:
            return normalized

        # Fallback when LLM output is invalid/unavailable.
        goal_url = _extract_first_url(goal)
        current_url = str(state.get("url") or "").strip()
        if goal_url and current_url and goal_url.rstrip("/") != current_url.rstrip("/"):
            return {
                "action": "goto",
                "url": goal_url,
                "selector": "",
                "text": "",
                "key": "",
                "attribute": "",
                "all_matches": False,
                "amount": 850,
                "reasoning": "navigate to target url",
            }
        if not current_url and goal_url:
            return {
                "action": "goto",
                "url": goal_url,
                "selector": "",
                "text": "",
                "key": "",
                "attribute": "",
                "all_matches": False,
                "amount": 850,
                "reasoning": "open initial url",
            }
        return {
            "action": "extract",
            "selector": "body",
            "text": "",
            "key": "",
            "url": "",
            "attribute": "",
            "all_matches": False,
            "amount": 850,
            "reasoning": "fallback extract page content",
        }

    async def act(self, action: Dict[str, Any]) -> Dict[str, Any]:
        plan = _normalize_action_payload(action if isinstance(action, dict) else {})
        action_name = plan.get("action", "")
        if not action_name:
            return {"ok": False, "action": "", "error": "missing action", "payload": {}}

        async def _do() -> Dict[str, Any]:
            if action_name == "goto":
                target = str(plan.get("url") or "").strip()
                if not target:
                    raise ValueError("goto requires url")
                if not self._started:
                    payload = await self.open(target)
                else:
                    payload = await self._browser.browser_goto(url=target)
                return {"ok": True, "action": action_name, "payload": payload}

            if action_name == "back":
                page = await self._ensure_page()
                await page.go_back(wait_until="domcontentloaded")
                return {"ok": True, "action": action_name, "payload": {"navigated": "back"}}

            if action_name == "click":
                selector = str(plan.get("selector") or "").strip()
                text = str(plan.get("text") or "").strip()
                if selector:
                    payload = await self._browser.browser_click(selector=selector)
                elif text:
                    payload = await self._browser.browser_click(text=text)
                else:
                    raise ValueError("click requires selector or text")
                return {"ok": True, "action": action_name, "payload": payload}

            if action_name == "fill":
                selector = str(plan.get("selector") or "").strip()
                text = str(plan.get("text") or "").strip()
                if not selector or not text:
                    raise ValueError("fill requires selector and text")
                payload = await self._browser.browser_fill(selector=selector, text=text)
                return {"ok": True, "action": action_name, "payload": payload}

            if action_name == "press":
                key = str(plan.get("key") or "").strip()
                selector = str(plan.get("selector") or "").strip()
                if not key:
                    raise ValueError("press requires key")
                if selector:
                    payload = await self._browser.browser_press(selector=selector, key=key)
                else:
                    page = await self._ensure_page()
                    await page.keyboard.press(key)
                    payload = {"key": key}
                return {"ok": True, "action": action_name, "payload": payload}

            if action_name == "scroll":
                amount = int(plan.get("amount", 850))
                page = await self._ensure_page()
                await page.mouse.wheel(0, amount)
                return {"ok": True, "action": action_name, "payload": {"amount": amount}}

            if action_name == "extract":
                selector = str(plan.get("selector") or "").strip() or "body"
                attribute = str(plan.get("attribute") or "").strip() or None
                all_matches = bool(plan.get("all_matches", False))
                payload = await self._browser.browser_extract(
                    selector=selector,
                    attribute=attribute,
                    all_matches=all_matches,
                )
                key = selector if selector != "body" else "body_text"
                if all_matches:
                    self.extracted_data[key] = list(payload.get("values") or [])
                else:
                    value = payload.get("value")
                    if value is not None:
                        self.extracted_data[key] = value
                return {"ok": True, "action": action_name, "payload": payload}

            if action_name in _DONE_ACTIONS:
                return {"ok": True, "action": action_name, "payload": {"done": True}}

            raise ValueError(f"unsupported action: {action_name}")

        try:
            result = await asyncio.wait_for(_do(), timeout=self.action_timeout_s)
        except Exception as exc:
            result = {
                "ok": False,
                "action": action_name,
                "error": str(exc),
                "payload": {},
            }

        self.actions_done.append(plan)
        self.results.append(result)
        return result

    async def verify(
        self,
        goal: str,
        *,
        state: Optional[Dict[str, Any]] = None,
        last_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_action = str((last_result or {}).get("action") or "").strip().lower()
        if last_action in _DONE_ACTIONS:
            return {"done": True, "summary": "Task completed.", "data": dict(self.extracted_data)}
        if last_action == "extract" and self.extracted_data:
            return {"done": True, "summary": "Information extracted successfully.", "data": dict(self.extracted_data)}
        if last_result is not None and not bool(last_result.get("ok")):
            return {"done": False, "summary": "Action failed; trying alternative path.", "data": dict(self.extracted_data)}

        check_payload = {
            "goal": goal,
            "state": {
                "url": (state or {}).get("url"),
                "title": (state or {}).get("title"),
                "dom_excerpt": _truncate_text(str((state or {}).get("dom_excerpt") or ""), 750),
                "vision_summary": _truncate_text(str((state or {}).get("vision_summary") or ""), 350),
                "recent_actions": self.actions_done[-4:],
                "recent_results": self.results[-4:],
                "data": self.extracted_data,
            },
        }
        try:
            payload = await self._llm_caller(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You verify web automation progress. Return JSON with keys: "
                            "done (bool), summary (string), data (object)."
                        ),
                    },
                    {"role": "user", "content": json.dumps(check_payload, ensure_ascii=False)},
                ],
                model=self.model,
                timeout_s=self._llm_timeout_s,
            )
        except Exception:
            payload = {}

        done = bool(payload.get("done", False))
        summary = str(payload.get("summary") or "").strip() or "In progress."
        data = payload.get("data")
        if isinstance(data, dict) and data:
            self.extracted_data.update(data)
        return {"done": done, "summary": summary, "data": dict(self.extracted_data)}

    def _action_fingerprint(self, decision: Dict[str, Any]) -> str:
        payload = {
            "action": str(decision.get("action") or ""),
            "selector": str(decision.get("selector") or ""),
            "text": str(decision.get("text") or ""),
            "url": str(decision.get("url") or ""),
            "key": str(decision.get("key") or ""),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)

    def _track_repetition(self, decision: Dict[str, Any], *, result: Dict[str, Any]) -> bool:
        if bool(result.get("ok")):
            self._repeated_fingerprint_count = 0
            self._last_fingerprint = ""
            return False
        fp = self._action_fingerprint(decision)
        if fp == self._last_fingerprint:
            self._repeated_fingerprint_count += 1
        else:
            self._last_fingerprint = fp
            self._repeated_fingerprint_count = 1
        return self._repeated_fingerprint_count >= 3

    async def run(self, goal: str) -> Dict[str, Any]:
        self._goal = str(goal or "").strip()
        self.pages_visited.clear()
        self.actions_done.clear()
        self.results.clear()
        self.extracted_data.clear()
        self._repeated_fingerprint_count = 0
        self._last_fingerprint = ""

        if not self._goal:
            return {"success": False, "summary": "Goal is empty.", "data": {}}

        initial_url = _extract_first_url(self._goal)
        if initial_url:
            try:
                await self.open(initial_url)
            except Exception as exc:
                return {
                    "success": False,
                    "summary": f"Failed to open target URL: {exc}",
                    "data": {},
                    "pages_visited": list(self.pages_visited),
                    "actions": list(self.actions_done),
                    "results": list(self.results),
                }
        elif not self._started:
            try:
                await self._browser.browser_launch()
                self._started = True
            except Exception as exc:
                return {
                    "success": False,
                    "summary": f"Failed to start browser session: {exc}",
                    "data": {},
                    "pages_visited": list(self.pages_visited),
                    "actions": list(self.actions_done),
                    "results": list(self.results),
                }

        done = False
        summary = "Goal not completed."
        for _step in range(1, self.max_steps + 1):
            state = await self.observe()
            decision = await self.decide(self._goal, state)
            decision_action = str(decision.get("action") or "").strip().lower()

            if decision_action in _DONE_ACTIONS:
                done = True
                summary = str(decision.get("reasoning") or "Task completed.").strip()
                break

            result = await self.act(decision)
            if self._track_repetition(decision, result=result):
                summary = "Stopped due to repeated failed actions (loop guard)."
                break

            verify_result = await self.verify(self._goal, state=state, last_result=result)
            done = bool(verify_result.get("done", False))
            summary = str(verify_result.get("summary") or summary).strip() or summary
            extracted = verify_result.get("data")
            if isinstance(extracted, dict) and extracted:
                self.extracted_data.update(extracted)
            if done:
                break
        else:
            summary = "Max steps reached before completion."

        if done and not summary:
            summary = "Task completed."
        if not done and not summary:
            summary = "Task could not be completed."

        return {
            "success": bool(done),
            "summary": _truncate_text(summary, 400),
            "data": dict(self.extracted_data),
            "pages_visited": list(self.pages_visited),
            "actions": list(self.actions_done),
            "results": list(self.results),
        }
