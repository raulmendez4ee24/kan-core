#!/usr/bin/env python3
"""
Dynamic API network probe with Playwright.

Use only against systems you own or are explicitly authorized to test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Response, async_playwright


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_9) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
]


def _is_json_content_type(content_type: str) -> bool:
    token = (content_type or "").lower()
    return "application/json" in token or "+json" in token or "text/json" in token


def _safe_json(text: str) -> Optional[str]:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _pretty_headers(headers: dict) -> str:
    try:
        return json.dumps(headers, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return str(headers)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discovered_apis (
              domain TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              method TEXT NOT NULL,
              request_headers TEXT NOT NULL,
              response_sample TEXT,
              timestamp TEXT NOT NULL,
              UNIQUE(domain, endpoint, method)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _upsert_discovered_api(
    *,
    db_path: str,
    domain: str,
    endpoint: str,
    method: str,
    request_headers_json: str,
    response_sample: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO discovered_apis (
              domain, endpoint, method, request_headers, response_sample, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, endpoint, method)
            DO UPDATE SET timestamp = excluded.timestamp
            """,
            (
                domain,
                endpoint,
                method,
                request_headers_json,
                response_sample,
                _utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def _build_context(browser: Browser, user_agent: str) -> BrowserContext:
    return await browser.new_context(
        user_agent=user_agent,
        locale="es-MX",
        extra_http_headers={"Accept-Language": "es-MX,es;q=0.9,en;q=0.8"},
        viewport={"width": 1400, "height": 900},
    )


async def run_probe(
    *,
    url: str,
    db_path: str,
    browser_name: str,
    timeout_ms: int,
    observe_seconds: int,
    max_body_chars: int,
) -> None:
    ua = random.choice(USER_AGENTS)
    domain = urlparse(url).netloc
    _init_db(db_path)
    print(f"[info] URL: {url}")
    print(f"[info] Domain: {domain}")
    print(f"[info] SQLite DB: {db_path}")
    print(f"[info] Browser: {browser_name}")
    print(f"[info] Headless: true")
    print(f"[info] User-Agent: {ua}")
    print("")

    async with async_playwright() as p:
        browser_launcher = getattr(p, browser_name)
        browser = await browser_launcher.launch(headless=True)
        context = await _build_context(browser, ua)
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        async def on_response(response: Response) -> None:
            request = response.request
            if request.resource_type not in {"xhr", "fetch"}:
                return

            content_type = response.headers.get("content-type", "")
            if not _is_json_content_type(content_type):
                return

            req_headers = await request.all_headers()
            req_headers_json = _pretty_headers(req_headers)
            body_text = ""
            body_err = None
            try:
                body_text = await response.text()
            except Exception as exc:
                body_err = str(exc)

            if body_text and len(body_text) > max_body_chars:
                body_text = body_text[:max_body_chars] + "\n...[truncated]..."

            pretty_json = _safe_json(body_text) if body_text else None

            print("=" * 88)
            print(f"ENDPOINT: {request.url}")
            print(f"METHOD:   {request.method}")
            print(f"STATUS:   {response.status}")
            print(f"TYPE:     {request.resource_type}")
            print(f"CT:       {content_type}")
            print("")
            print("REQUEST HEADERS:")
            print(req_headers_json)

            post_data = request.post_data
            if post_data:
                trimmed = post_data[:max_body_chars]
                if len(post_data) > max_body_chars:
                    trimmed += "\n...[truncated]..."
                print("")
                print("REQUEST BODY:")
                print(trimmed)

            print("")
            print("RESPONSE BODY:")
            if body_err:
                print(f"[error reading body] {body_err}")
            elif pretty_json is not None:
                print(pretty_json)
            else:
                print(body_text or "[empty]")

            response_sample = (
                pretty_json
                if pretty_json is not None
                else (body_text or ("[error] " + body_err if body_err else "[empty]"))
            )
            if len(response_sample) > max_body_chars:
                response_sample = response_sample[:max_body_chars] + "\n...[truncated]..."

            _upsert_discovered_api(
                db_path=db_path,
                domain=domain,
                endpoint=request.url,
                method=request.method,
                request_headers_json=req_headers_json,
                response_sample=response_sample,
            )
            print("[db] upserted discovered_apis")
            print("=" * 88)
            print("")

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")
        except Exception as exc:
            print(f"[warn] initial navigation warning: {exc}")

        await asyncio.sleep(max(1, observe_seconds))
        await context.close()
        await browser.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Intercepta requests fetch/xhr JSON y muestra endpoints internos usados por una pagina."
    )
    parser.add_argument("url", help="URL objetivo (ej: https://example.com)")
    parser.add_argument(
        "--db-path",
        default="discovered_apis.db",
        help="Ruta del archivo SQLite",
    )
    parser.add_argument(
        "--browser",
        default="chromium",
        choices=["chromium", "firefox", "webkit"],
        help="Motor Playwright",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Timeout por accion en milisegundos",
    )
    parser.add_argument(
        "--observe-seconds",
        type=int,
        default=8,
        help="Tiempo extra para observar trafico luego de cargar la pagina",
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=3000,
        help="Maximo de caracteres a imprimir para request/response body",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(
        run_probe(
            url=args.url,
            db_path=args.db_path,
            browser_name=args.browser,
            timeout_ms=args.timeout_ms,
            observe_seconds=args.observe_seconds,
            max_body_chars=args.max_body_chars,
        )
    )


if __name__ == "__main__":
    main()
