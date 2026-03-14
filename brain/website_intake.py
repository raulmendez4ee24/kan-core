import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx

_API_DOC_PATH_HINTS = (
    "openapi",
    "swagger",
    "api-docs",
    "apidocs",
    "redoc",
    "postman",
    "/v1/",
    "/v2/",
)


class _TextAndLinksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignore_depth = 0
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self.links: List[str] = []
        self._inside_title = False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if lowered == "title":
            self._inside_title = True
            return
        if lowered == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.links.append(value.strip())

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
            return
        if lowered == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._inside_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)


def _looks_like_public_ip(hostname: str) -> bool:
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _is_public_hostname(hostname: str) -> bool:
    lowered = hostname.lower().strip()
    if not lowered or lowered == "localhost" or lowered.endswith(".local"):
        return False

    if not _looks_like_public_ip(lowered):
        return False

    try:
        infos = socket.getaddrinfo(lowered, None)
    except socket.gaierror:
        return False

    for info in infos:
        resolved_ip = info[4][0]
        if not _looks_like_public_ip(resolved_ip):
            return False
    return True


def _normalize_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:max_chars]


def _find_api_docs_links(base_url: str, raw_links: List[str], max_links: int) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in raw_links:
        candidate = urljoin(base_url, raw)
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        path = parsed.path.lower()
        hint = f"{path}?{parsed.query}".lower()
        if not any(token in hint for token in _API_DOC_PATH_HINTS):
            continue
        clean = parsed._replace(fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
        if len(normalized) >= max_links:
            break
    return normalized


async def inspect_target_website(
    target_url: str,
    *,
    timeout_seconds: float = 8.0,
    max_text_chars: int = 4000,
    max_api_links: int = 20,
) -> Dict[str, object]:
    parsed = urlparse(target_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return {
            "processed": False,
            "website_url": None,
            "website_text": "",
            "api_docs_urls": [],
            "notes": ["La URL objetivo debe usar http o https."],
        }
    if not parsed.hostname or not _is_public_hostname(parsed.hostname):
        return {
            "processed": False,
            "website_url": None,
            "website_text": "",
            "api_docs_urls": [],
            "notes": ["No se permite inspeccionar hosts locales o privados."],
        }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
            response = await client.get(
                parsed.geturl(),
                headers={"User-Agent": "kan-core-intake-bot/1.0"},
            )
        response.raise_for_status()
    except Exception as exc:
        return {
            "processed": False,
            "website_url": None,
            "website_text": "",
            "api_docs_urls": [],
            "notes": [f"No se pudo leer la URL objetivo: {exc!s}"],
        }

    content_type = (response.headers.get("content-type") or "").lower()
    final_url = str(response.url)

    # Si es JSON y parece OpenAPI, lo tratamos como doc detectada.
    if "application/json" in content_type:
        body = response.text or ""
        looks_openapi = '"openapi"' in body[:5000] or '"swagger"' in body[:5000]
        return {
            "processed": True,
            "website_url": final_url,
            "website_text": "",
            "api_docs_urls": [final_url] if looks_openapi else [],
            "notes": [],
        }

    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        return {
            "processed": True,
            "website_url": final_url,
            "website_text": "",
            "api_docs_urls": [],
            "notes": [f"Tipo de contenido no HTML ({content_type or 'desconocido'})."],
        }

    parser = _TextAndLinksParser()
    parser.feed(response.text)
    page_title = " ".join(parser.title_parts).strip()
    page_text = _normalize_text(" ".join(parser.text_parts), max_text_chars=max_text_chars)
    combined_text = _normalize_text(f"{page_title}. {page_text}", max_text_chars=max_text_chars)
    api_docs_urls = _find_api_docs_links(
        final_url,
        parser.links,
        max_links=max_api_links,
    )

    return {
        "processed": True,
        "website_url": final_url,
        "website_text": combined_text,
        "api_docs_urls": api_docs_urls,
        "notes": [],
    }
