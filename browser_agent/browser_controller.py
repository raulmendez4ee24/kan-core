import asyncio
import base64
import io
import os
from urllib.parse import urlparse

from PIL import Image
from playwright.async_api import async_playwright


class BrowserController:
    def __init__(
        self,
        *,
        enabled: bool,
        headless: bool,
        channel: str,
        user_data_dir: str,
        domain_allowlist: list[str],
        allow_all_domains: bool = False,
        default_timeout_ms: int,
    ) -> None:
        self._enabled = bool(enabled)
        self._headless = bool(headless)
        preferred_channel = channel.strip().lower() if isinstance(channel, str) else "chromium"
        # Force Playwright bundled chromium to avoid macOS system Chrome permission issues.
        self._channel = "chromium" if preferred_channel != "chromium" else preferred_channel
        self._user_data_dir = user_data_dir.strip() if isinstance(user_data_dir, str) else ""
        self._domain_allowlist = self._normalize_allowlist(domain_allowlist)
        self._allow_all_domains = bool(allow_all_domains)
        self._default_timeout_ms = max(1000, int(default_timeout_ms))

        self._playwright = None
        self._context = None
        self._page = None
        self._session_lock = asyncio.Lock()

    @staticmethod
    def _normalize_allowlist(items: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in items:
            token = str(item).strip().lower()
            if token and token not in normalized:
                normalized.append(token)
        return normalized

    def _ensure_enabled(self) -> None:
        if not self._enabled:
            raise RuntimeError("Browser agent is disabled")

    def _is_domain_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        if self._allow_all_domains:
            return True
        if not self._domain_allowlist:
            return False
        return any(host == domain or host.endswith(f".{domain}") for domain in self._domain_allowlist)

    def _require_allowed_url(self, url: str) -> None:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            raise ValueError("URL must use http or https")
        if not self._is_domain_allowed(url):
            raise ValueError("URL domain is not in browser allowlist")

    async def _ensure_session(self) -> None:
        self._ensure_enabled()

        async with self._session_lock:
            if self._page is not None and self._context is not None:
                return

            profile_dir = self._user_data_dir or os.path.join(os.getcwd(), ".browser_profile")
            os.makedirs(profile_dir, exist_ok=True)

            pw = await async_playwright().start()
            self._playwright = pw

            launch_kwargs: dict = {
                "user_data_dir": profile_dir,
                "headless": self._headless,
                "channel": "chromium",
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            }

            self._context = await pw.chromium.launch_persistent_context(**launch_kwargs)
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            self._page.set_default_timeout(self._default_timeout_ms)

    async def _ensure_interaction_page(self) -> None:
        await self._ensure_session()
        if self._page is None:
            raise RuntimeError("Browser page not initialized")
        current_url = str(self._page.url or "")
        if current_url.startswith("http") and not self._is_domain_allowed(current_url):
            raise RuntimeError("Current page domain is not in browser allowlist")

    async def browser_launch(
        self,
        *,
        headless: bool | None = None,
        channel: str | None = None,
        start_url: str | None = None,
    ) -> dict:
        if headless is not None:
            self._headless = bool(headless)
        if isinstance(channel, str) and channel.strip():
            # Keep bundled chromium only for reliability in autonomous runtime.
            self._channel = "chromium"

        await self.browser_close()
        await self._ensure_session()
        if start_url:
            self._require_allowed_url(start_url)
            await self._page.goto(start_url, wait_until="domcontentloaded")
        return {
            "launched": True,
            "channel": self._channel,
            "headless": self._headless,
            "url": str(self._page.url or ""),
            "title": str(await self._page.title() or ""),
        }

    async def browser_goto(self, *, url: str, wait_until: str = "domcontentloaded") -> dict:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("browser_goto requires url")
        self._require_allowed_url(url)
        await self._ensure_session()
        await self._page.goto(url.strip(), wait_until=str(wait_until or "domcontentloaded"))
        return {"url": str(self._page.url or ""), "title": str(await self._page.title() or "")}

    async def browser_click(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        role: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        await self._ensure_interaction_page()
        timeout = int(timeout_ms) if timeout_ms is not None else self._default_timeout_ms
        if (x is None) != (y is None):
            raise ValueError("browser_click requires x and y together when using coordinates")
        if x is not None and y is not None:
            await self._page.mouse.click(int(x), int(y))
            return {"mode": "coordinates", "x": int(x), "y": int(y)}

        if isinstance(selector, str) and selector.strip():
            await self._page.locator(selector.strip()).first.click(timeout=timeout)
            return {"mode": "selector", "selector": selector.strip()}
        if isinstance(text, str) and text.strip():
            await self._page.get_by_text(text.strip()).first.click(timeout=timeout)
            return {"mode": "text", "text": text.strip()}
        if isinstance(role, str) and role.strip():
            locator = self._page.get_by_role(
                role.strip(),
                name=(name.strip() if isinstance(name, str) else None),
            )
            await locator.first.click(timeout=timeout)
            return {
                "mode": "role",
                "role": role.strip(),
                "name": (name.strip() if isinstance(name, str) else None),
            }
        raise ValueError("browser_click requires selector, text, role, or x/y")

    async def browser_fill(
        self,
        *,
        selector: str,
        text: str,
        clear_first: bool = True,
        timeout_ms: int | None = None,
    ) -> dict:
        await self._ensure_interaction_page()
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("browser_fill requires selector")
        if not isinstance(text, str):
            raise ValueError("browser_fill requires text")
        timeout = int(timeout_ms) if timeout_ms is not None else self._default_timeout_ms
        locator = self._page.locator(selector.strip()).first
        if clear_first:
            await locator.fill("", timeout=timeout)
        await locator.fill(text, timeout=timeout)
        return {"selector": selector.strip(), "text_length": len(text)}

    async def browser_press(
        self,
        *,
        selector: str,
        key: str,
        timeout_ms: int | None = None,
    ) -> dict:
        await self._ensure_interaction_page()
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("browser_press requires selector")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("browser_press requires key")
        timeout = int(timeout_ms) if timeout_ms is not None else self._default_timeout_ms
        await self._page.locator(selector.strip()).first.press(key.strip(), timeout=timeout)
        return {"selector": selector.strip(), "key": key.strip()}

    async def browser_extract(
        self,
        *,
        selector: str,
        attribute: str | None = None,
        all_matches: bool = False,
        timeout_ms: int | None = None,
    ) -> dict:
        await self._ensure_interaction_page()
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("browser_extract requires selector")
        timeout = int(timeout_ms) if timeout_ms is not None else self._default_timeout_ms
        locator = self._page.locator(selector.strip())
        await locator.first.wait_for(timeout=timeout)

        if all_matches:
            count = await locator.count()
            values = []
            for idx in range(min(count, 100)):
                item = locator.nth(idx)
                if attribute:
                    values.append(await item.get_attribute(attribute))
                else:
                    values.append(await item.inner_text())
            return {
                "selector": selector.strip(),
                "attribute": attribute,
                "values": values,
                "count": len(values),
            }

        item = locator.first
        if attribute:
            value = await item.get_attribute(attribute)
        else:
            value = await item.inner_text()
        return {"selector": selector.strip(), "attribute": attribute, "value": value}

    async def browser_screenshot(self, *, full_page: bool = False) -> dict:
        await self._ensure_interaction_page()
        await self._page.wait_for_load_state("networkidle")
        await asyncio.sleep(0.5)
        image_bytes = await self._page.screenshot(full_page=bool(full_page), type="png")
        image = Image.open(io.BytesIO(image_bytes))
        return {
            "mime_type": "image/png",
            "width": int(image.width),
            "height": int(image.height),
            "image_base64": base64.b64encode(image_bytes).decode(),
            "url": str(self._page.url or ""),
        }

    async def browser_close(self) -> dict:
        async with self._session_lock:
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:
                    pass
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
            self._context = None
            self._page = None
            self._playwright = None
        return {"closed": True}
