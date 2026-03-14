"""
Lead generator: Google Places + website scraping + email validation.
Output compatible with Cold Email Ops schema (lead_id, company, website, ...).
Production-ready, modular, async scraping, CSV/Sheets export.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import csv
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

log = logging.getLogger("kan_core.lead_generator")


class PlacesQuotaExceeded(Exception):
    """Raised when Places API returns OVER_QUERY_LIMIT or HTTP 429."""


LEAD_SCHEMA_KEYS = [
    "lead_id", "company", "website", "first_name", "last_name", "title",
    "email", "linkedin", "country", "segment", "status", "last_contacted_at",
    "source", "notes", "personalization_snippet", "email_validation_status", "score",
]

DEFAULT_COUNTRY = "MX"
DEFAULT_SEGMENT = "local_business"
DEFAULT_STATUS = "new"
DEFAULT_SOURCE = "google_places"
DEFAULT_SCORE_BASE = 70
PLACES_BASE = "https://maps.googleapis.com/maps/api/place"
REQUEST_TIMEOUT = 15.0
SCRAPE_TIMEOUT = 10.0
MAX_CONCURRENT_SCRAPES = 8

# Extra paths for email discovery (in addition to /contact, /about, etc.)
SCRAPE_PATHS = [
    "/contact", "/about", "/team", "/contacto", "/nosotros",
    "/contact-us", "/about-us", "/staff", "/equipo",
]

# Ignore these in email extraction (generic/malformed)
EMAIL_BLACKLIST_DOMAINS = {"example.com", "example.org", "sentry.io", "wixpress.com", "domain.invalid"}
EMAIL_BLACKLIST_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
# Preferred local parts for "best" email (role-based)
EMAIL_PREFERRED_LOCAL = {"info", "contact", "hola", "ventas", "contacto", "sales", "hello"}
# Avoid these (junk/noreply)
EMAIL_AVOID_LOCAL = {"noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster"}


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def clean_domain(url: str) -> str:
    """
    Return only domain: remove http(s):// and trailing slash.
    Example: https://clinicadental.com/ → clinicadental.com
    """
    if not url or not isinstance(url, str):
        return ""
    s = url.strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = s.rstrip("/").split("/")[0].split("?")[0]
    return s


def _base_url(website: str) -> str:
    s = (website or "").strip().lower()
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s.rstrip("/")


EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)


@retry(
    retry=retry_if_exception(lambda e: isinstance(e, PlacesQuotaExceeded)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _places_request(client: httpx.AsyncClient, path: str, params: Dict[str, str]) -> Dict[str, Any]:
    url = f"{PLACES_BASE}{path}"
    r = await client.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code == 429:
        raise PlacesQuotaExceeded("Places API rate limit (429)")
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "REQUEST_DENIED":
        raise ValueError(data.get("error_message", "Places API request denied"))
    if data.get("status") == "OVER_QUERY_LIMIT":
        raise PlacesQuotaExceeded("Places API quota exceeded")
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise ValueError(f"Places API status: {data.get('status')}")
    return data


async def search_places(
    keyword: str,
    location: str,
    api_key: str,
    max_results: int = 100,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Search businesses using Google Places API (Text Search + Details)."""
    if not api_key:
        log.warning("GOOGLE_API_KEY missing, skip Places search")
        return []
    query = f"{keyword} in {location}"
    all_places: List[Dict[str, Any]] = []
    next_page_token: Optional[str] = None
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient()
    try:
        while len(all_places) < max_results:
            params: Dict[str, str] = {"query": query, "key": api_key}
            if next_page_token:
                params["pagetoken"] = next_page_token
            data = await _places_request(client, "/textsearch/json", params)
            results = data.get("results") or []
            if not results:
                break
            log.info("Found %s businesses (page)...", len(results))
            for r in results:
                if len(all_places) >= max_results:
                    break
                place_id = r.get("place_id")
                if not place_id:
                    continue
                detail_params = {
                    "place_id": place_id,
                    "key": api_key,
                    "fields": "name,formatted_address,formatted_phone_number,international_phone_number,website,url,rating,user_ratings_total",
                }
                try:
                    detail_data = await _places_request(client, "/details/json", detail_params)
                    res = detail_data.get("result") or {}
                except Exception as e:
                    log.debug("Place details failed for %s: %s", place_id, e)
                    res = {"name": r.get("name"), "formatted_address": r.get("formatted_address"), "rating": r.get("rating"), "url": f"https://www.google.com/maps/place/?q=place_id:{place_id}"}
                all_places.append({
                    "name": res.get("name") or "",
                    "website": (res.get("website") or "").strip() or None,
                    "phone": (res.get("formatted_phone_number") or res.get("international_phone_number") or "").strip() or None,
                    "address": (res.get("formatted_address") or "").strip() or None,
                    "rating": res.get("rating"),
                    "user_ratings_total": res.get("user_ratings_total"),
                    "maps_url": (res.get("url") or f"https://www.google.com/maps/place/?q=place_id:{place_id}").strip(),
                    "place_id": place_id,
                })
            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break
            await asyncio.sleep(1.5)
        log.info("Found %s businesses total.", len(all_places))
        return all_places[:max_results]
    finally:
        if own_client and client:
            await client.aclose()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def scrape_website(url: str, client: Optional[httpx.AsyncClient] = None) -> str:
    """Fetch URL and return text for email extraction. Retries on timeout/SSL/HTTP errors."""
    url = _base_url(url)
    if not url:
        return ""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=True)
    try:
        r = await client.get(url, timeout=SCRAPE_TIMEOUT)
        r.raise_for_status()
        html = r.text
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True)
        return html
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError, Exception) as e:
        log.debug("Scrape %s failed: %s", url, e)
        return ""
    finally:
        if own_client and client:
            await client.aclose()


def extract_emails(text: str) -> List[str]:
    """Extract emails via regex, deduplicate, and filter generic/malformed."""
    if not text:
        return []
    found = EMAIL_REGEX.findall(text)
    out: List[str] = []
    for e in found:
        if not e:
            continue
        e = e.strip().lower()
        if len(e) > 254 or ".." in e or e.startswith(".") or e.endswith("."):
            continue
        if any(e.endswith(s) for s in EMAIL_BLACKLIST_SUFFIXES):
            continue
        if "@" in e:
            domain = e.rsplit("@", 1)[1]
            if domain in EMAIL_BLACKLIST_DOMAINS:
                continue
            if e.startswith("image@") or e.startswith("img@"):
                continue
        out.append(e)
    return list(dict.fromkeys(out))


async def _scrape_paths(base_url: str, paths: List[str], semaphore: asyncio.Semaphore, client: httpx.AsyncClient) -> List[str]:
    all_emails: List[str] = []
    urls = [base_url] + [urljoin(base_url + "/", p) for p in paths]

    async def fetch(u: str) -> List[str]:
        async with semaphore:
            text = await scrape_website(u, client)
            return extract_emails(text)

    results = await asyncio.gather(*[fetch(u) for u in urls], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            all_emails.extend(r)
    return list(dict.fromkeys(e.lower() for e in all_emails))


def validate_email_format(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local, domain = email.rsplit("@", 1)
    if not local or not domain or ".." in email:
        return False
    if not re.match(r"^[a-zA-Z0-9._%+-]+$", local):
        return False
    if not re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", domain):
        return False
    return True


def validate_email(email: str, smtp_check: bool = False) -> str:
    """Returns: valid | risky | invalid | unknown."""
    if not email:
        return "invalid"
    email = email.strip().lower()
    if not validate_email_format(email):
        return "invalid"
    if smtp_check:
        try:
            import dns.resolver
            domain = email.rsplit("@", 1)[1]
            dns.resolver.resolve(domain, "MX")
            return "valid"
        except Exception:
            return "risky"
    return "unknown"


def _email_local_part(email: str) -> str:
    return email.split("@")[0].lower() if "@" in email else ""


def pick_best_email(emails: List[str], smtp_check: bool = False) -> Tuple[str, str]:
    """
    Choose one best email from list. Returns (email, status).
    Prefer: valid > risky/unknown; role-based (info@, contact@, hola@, ventas@);
    avoid noreply@ and junk. If none valid, return first risky or empty with status.
    """
    if not emails:
        return ("", "unknown")
    validated: List[Tuple[str, str]] = []
    for e in emails:
        e = (e or "").strip().lower()
        if not e:
            continue
        local = _email_local_part(e)
        if local in EMAIL_AVOID_LOCAL:
            continue
        status = validate_email(e, smtp_check=smtp_check)
        validated.append((e, status))
    if not validated:
        return ("", "unknown")
    valid_list = [x for x in validated if x[1] == "valid"]
    if valid_list:
        for e, s in valid_list:
            if _email_local_part(e) in EMAIL_PREFERRED_LOCAL:
                return (e, s)
        return valid_list[0]
    other = [x for x in validated if x[1] in ("risky", "unknown")]
    if other:
        for e, s in other:
            if _email_local_part(e) in EMAIL_PREFERRED_LOCAL:
                return (e, s)
        return other[0]
    return ("", "invalid")


def generate_lead(
    lead_id: str,
    company: str,
    website: Optional[str] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    rating: Optional[float] = None,
    maps_url: Optional[str] = None,
    phone: Optional[str] = None,
    place_id: Optional[str] = None,
    location: str = "",
    country: str = DEFAULT_COUNTRY,
    segment: str = DEFAULT_SEGMENT,
    status: str = DEFAULT_STATUS,
    source: str = DEFAULT_SOURCE,
    email_validation_status: str = "unknown",
) -> Dict[str, Any]:
    """
    Build lead dict for Cold Email Ops schema.
    Score: base 70, +10 if website, +10 if valid email, +5 if rating >= 4.5, cap 100.
    Includes extra keys for debugging: place_id, phone, rating, maps_url (CSV export ignores these).
    """
    score = DEFAULT_SCORE_BASE
    if website:
        score += 10
    if email and email_validation_status == "valid":
        score += 10
    if rating is not None and rating >= 4.5:
        score += 5
    score = min(100, score)

    loc = (location or "").strip()
    if company and rating is not None and rating >= 4.0 and loc:
        personalization_snippet = f"I saw {company} has strong reviews in {loc} (rating {rating})."
    elif company and loc:
        personalization_snippet = f"I came across {company} while researching businesses in {loc}."
    else:
        personalization_snippet = ""

    out: Dict[str, Any] = {
        "lead_id": lead_id,
        "company": company or "",
        "website": (clean_domain(website) if website else "") or "",
        "first_name": "",
        "last_name": "",
        "title": "",
        "email": (email or "").strip() or "",
        "linkedin": "",
        "country": country,
        "segment": segment,
        "status": status,
        "last_contacted_at": "",
        "source": source,
        "notes": (address or "")[:500],
        "personalization_snippet": personalization_snippet[:500],
        "email_validation_status": email_validation_status,
        "score": score,
    }
    # Internal/debug fields (not in CSV schema)
    out["place_id"] = (place_id or "").strip()
    out["phone"] = (phone or "").strip()
    out["rating"] = rating
    out["maps_url"] = (maps_url or "").strip()
    return out


def _normalize_for_dedupe(s: str) -> str:
    """Normalize string for dedupe key: lower, strip, collapse spaces."""
    if not s or not isinstance(s, str):
        return ""
    return " ".join((s.strip().lower()).split())


def deduplicate_leads(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Dedupe: prefer place_id (unique from Google Places); if no website use (normalized_company, address);
    else (domain, company).
    """
    seen: Set[tuple] = set()
    out: List[Dict[str, Any]] = []
    for lead in leads:
        place_id = (lead.get("place_id") or "").strip()
        domain = (lead.get("website") or "").strip().lower()
        company = _normalize_for_dedupe(lead.get("company") or "")
        address = _normalize_for_dedupe(lead.get("notes") or lead.get("address") or "")

        if place_id:
            key = ("place_id", place_id)
        elif domain:
            key = ("domain", domain, company or "")
        else:
            key = ("no_domain", company or "", address or "")

        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
    return out


async def run_lead_generation(
    keyword: Optional[str] = None,
    location: Optional[str] = None,
    api_key: Optional[str] = None,
    max_leads: int = 100,
    smtp_check: bool = False,
    export_csv_path: Optional[str] = None,
    export_sheets_id: Optional[str] = None,
    export_sheets_upsert: bool = False,
) -> List[Dict[str, Any]]:
    keyword = keyword or _env("KEYWORD", "dentist")
    location = location or _env("LOCATION", "Guadalajara Mexico")
    api_key = api_key or _env("GOOGLE_API_KEY")
    max_leads = max_leads or int(_env("MAX_LEADS", "100") or "100")
    smtp_check = smtp_check or _env("SMTP_CHECK", "false").lower() in ("1", "true", "yes")
    if not api_key:
        raise ValueError("Missing GOOGLE_API_KEY")
    log.info("Starting lead generation: keyword=%r, location=%r, max_leads=%s", keyword, location, max_leads)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        places = await search_places(keyword, location, api_key, max_results=max_leads, client=client)
    if not places:
        log.warning("No places found.")
        return []
    log.info("Found %s businesses...", len(places))
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCRAPES)
    leads: List[Dict[str, Any]] = []
    next_lead_id = 1
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i, place in enumerate(places):
            try:
                name = (place.get("name") or "").strip()
                website = place.get("website")
                address = place.get("address")
                rating = place.get("rating")
                maps_url = place.get("maps_url")
                phone = place.get("phone")
                place_id = (place.get("place_id") or "").strip()
                log.info("Processing %s/%s: %s", i + 1, len(places), name or "(no name)")

                emails: List[str] = []
                if website:
                    log.info("Scraping website...")
                    emails = await _scrape_paths(
                        _base_url(website),
                        SCRAPE_PATHS,
                        semaphore,
                        client,
                    )
                    log.info("Emails found: %s", len(emails))

                chosen_email, email_val = pick_best_email(emails, smtp_check=smtp_check)

                lead = generate_lead(
                    str(next_lead_id),
                    company=name,
                    website=website,
                    email=chosen_email or None,
                    address=address,
                    rating=rating,
                    maps_url=maps_url,
                    phone=phone,
                    place_id=place_id,
                    location=location,
                    email_validation_status=email_val,
                )
                leads.append(lead)
                next_lead_id += 1
            except Exception as e:
                log.warning("Skipping place %s due to error: %s", i + 1, e)

    leads = deduplicate_leads(leads)
    log.info("Generated %s leads (after dedupe).", len(leads))
    if export_csv_path:
        export_csv(leads, export_csv_path)
    if export_sheets_id:
        export_sheets(leads, export_sheets_id, upsert=export_sheets_upsert)
    log.info("Export completed.")
    return leads


def export_csv(leads: List[Dict[str, Any]], path: str) -> None:
    """Write CSV with columns exactly matching Cold Email Ops leads schema."""
    if not leads:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEAD_SCHEMA_KEYS, extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)
    log.info("Exported %s leads to %s", len(leads), path)


def export_sheets(leads: List[Dict[str, Any]], spreadsheet_id: str, upsert: bool = False) -> bool:
    """Export leads to Google Sheet. Ensure leads tab has headers (create if missing). Append rows; if upsert=True, update by lead_id when already present."""
    if not leads:
        return True
    try:
        from tools.google_sheets_client import ensure_worksheet_with_headers, open_spreadsheet
        ensure_worksheet_with_headers(spreadsheet_id, "leads", LEAD_SCHEMA_KEYS)
        book = open_spreadsheet(spreadsheet_id)
        sheet = book.worksheet("leads")
        keys = LEAD_SCHEMA_KEYS
        all_values = sheet.get_all_values()
        existing_ids = _collect_existing_lead_ids(all_values)
        prepared_leads = _prepare_leads_for_sheet(leads, existing_ids, preserve_existing_ids=upsert)
        if upsert:
            lead_id_to_row: Dict[str, int] = {}
            for i, row in enumerate(all_values):
                if i == 0:
                    continue
                if row and len(row) >= 1:
                    lead_id_to_row[str(row[0]).strip()] = i + 1
            to_append: List[List[Any]] = []
            for lead in prepared_leads:
                lid = str(lead.get("lead_id", "")).strip()
                row_data = [lead.get(k, "") for k in keys]
                if lid and lid in lead_id_to_row:
                    row_idx = lead_id_to_row[lid]
                    for c, val in enumerate(row_data):
                        sheet.update_cell(row_idx, c + 1, val)
                else:
                    to_append.append(row_data)
            if to_append:
                sheet.append_rows(to_append, value_input_option="USER_ENTERED")
        else:
            rows = [[lead.get(k, "") for k in keys] for lead in prepared_leads]
            sheet.append_rows(rows, value_input_option="USER_ENTERED")
        _sync_lead_ids(leads, prepared_leads)
        log.info("Exported %s leads to Google Sheet %s", len(prepared_leads), spreadsheet_id)
        return True
    except Exception as e:
        log.warning("Export to Sheets failed: %s", e)
        return False


def _collect_existing_lead_ids(all_values: List[List[Any]]) -> Set[int]:
    existing_ids: Set[int] = set()
    for i, row in enumerate(all_values):
        if i == 0 or not row:
            continue
        raw = str(row[0]).strip()
        if raw.isdigit():
            existing_ids.add(int(raw))
    return existing_ids


def _next_available_lead_id(used_ids: Set[int]) -> int:
    return (max(used_ids) + 1) if used_ids else 1


def _prepare_leads_for_sheet(
    leads: List[Dict[str, Any]],
    existing_ids: Set[int],
    *,
    preserve_existing_ids: bool,
) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    used_ids = set(existing_ids)
    seen_ids_in_batch: Set[int] = set()
    next_id = _next_available_lead_id(used_ids)

    for lead in leads:
        row = dict(lead)
        current_id = _parse_lead_id(row.get("lead_id"))
        should_preserve = (
            current_id is not None
            and current_id not in seen_ids_in_batch
            and (current_id not in existing_ids or preserve_existing_ids)
        )
        if should_preserve:
            assigned_id = current_id
        else:
            while next_id in used_ids or next_id in seen_ids_in_batch:
                next_id += 1
            assigned_id = next_id
            next_id += 1
        row["lead_id"] = str(assigned_id)
        used_ids.add(assigned_id)
        seen_ids_in_batch.add(assigned_id)
        prepared.append(row)
    return prepared


def _parse_lead_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    return int(raw) if raw.isdigit() else None


def _sync_lead_ids(original_leads: List[Dict[str, Any]], prepared_leads: List[Dict[str, Any]]) -> None:
    for original, prepared in zip(original_leads, prepared_leads):
        original["lead_id"] = prepared.get("lead_id", original.get("lead_id"))
