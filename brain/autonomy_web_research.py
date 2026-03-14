import os
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
from observability import capture_exception
from schemas.desktop_autonomy import (
    DesktopAutonomyResearchRunRequest,
    DesktopAutonomyResearchRunResponse,
    DesktopAutonomyResearchSummary,
    DesktopAutonomyRunRequest,
    ResearchSourceItem,
)
from tools.retry import request_with_retry


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_queries(goal: str) -> List[str]:
    base = _clean_text(goal)
    queries = [
        base,
        f"{base} official documentation latest updates",
        f"{base} troubleshooting best practices examples",
    ]
    deduped: List[str] = []
    seen = set()
    for query in queries:
        token = query.lower()
        if token in seen:
            continue
        seen.add(token)
        deduped.append(query)
    return deduped


def _normalize_tavily_results(raw: Dict[str, Any], query: str) -> List[ResearchSourceItem]:
    items = raw.get("results")
    if not isinstance(items, list):
        return []
    normalized: List[ResearchSourceItem] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        url = _clean_text(entry.get("url"))
        if not url:
            continue
        normalized.append(
            ResearchSourceItem(
                title=_clean_text(entry.get("title")) or "Untitled",
                url=url,
                snippet=_clean_text(entry.get("content")),
                provider="tavily",
                query=query,
                published_at=_clean_text(entry.get("published_date")) or None,
            )
        )
    return normalized


def _normalize_brave_results(raw: Dict[str, Any], query: str) -> List[ResearchSourceItem]:
    web = raw.get("web")
    if not isinstance(web, dict):
        return []
    items = web.get("results")
    if not isinstance(items, list):
        return []
    normalized: List[ResearchSourceItem] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        url = _clean_text(entry.get("url"))
        if not url:
            continue
        normalized.append(
            ResearchSourceItem(
                title=_clean_text(entry.get("title")) or "Untitled",
                url=url,
                snippet=_clean_text(entry.get("description")),
                provider="brave",
                query=query,
                published_at=_clean_text(entry.get("age")) or None,
            )
        )
    return normalized


async def _search_tavily(query: str, *, max_results: int) -> List[ResearchSourceItem]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
    }
    timeout = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "12"))
    retries = int(os.getenv("SEARCH_RETRIES", "3"))
    backoff_base = float(os.getenv("SEARCH_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("SEARCH_BACKOFF_MAX", "4.0"))
    try:
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                "POST",
                "https://api.tavily.com/search",
                json=payload,
                timeout=timeout,
                retries=retries,
                backoff_base=backoff_base,
                backoff_max=backoff_max,
            )
        if response.status_code >= 400:
            return []
        data = response.json()
        if not isinstance(data, dict):
            return []
        return _normalize_tavily_results(data, query)
    except Exception as exc:
        capture_exception(exc, {"service": "tavily_search", "query": query})
        return []


async def _search_brave(query: str, *, max_results: int) -> List[ResearchSourceItem]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return []

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": query,
        "count": str(max_results),
    }
    timeout = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "12"))
    retries = int(os.getenv("SEARCH_RETRIES", "3"))
    backoff_base = float(os.getenv("SEARCH_BACKOFF_BASE", "0.5"))
    backoff_max = float(os.getenv("SEARCH_BACKOFF_MAX", "4.0"))
    try:
        async with httpx.AsyncClient() as client:
            response = await request_with_retry(
                client,
                "GET",
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
                timeout=timeout,
                retries=retries,
                backoff_base=backoff_base,
                backoff_max=backoff_max,
            )
        if response.status_code >= 400:
            return []
        data = response.json()
        if not isinstance(data, dict):
            return []
        return _normalize_brave_results(data, query)
    except Exception as exc:
        capture_exception(exc, {"service": "brave_search", "query": query})
        return []


def _dedupe_sources(items: List[ResearchSourceItem]) -> List[ResearchSourceItem]:
    deduped: List[ResearchSourceItem] = []
    seen = set()
    for item in items:
        url = item.url.strip().lower()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped


def _build_research_goal(goal: str, sources: List[ResearchSourceItem]) -> str:
    lines = [goal.strip(), "", "Web evidence (verified sources):"]
    for item in sources[:8]:
        lines.append(f"- {item.title} | {item.url}")
    return "\n".join(lines).strip()


def _build_research_summary(
    *,
    queries: List[str],
    notes: List[str],
    sources: List[ResearchSourceItem],
    min_sources: int,
) -> DesktopAutonomyResearchSummary:
    deduped = _dedupe_sources(sources)
    providers = sorted({item.provider for item in deduped})
    unique_sources = len(deduped)
    return DesktopAutonomyResearchSummary(
        queries=queries,
        providers_used=providers,
        total_sources=len(sources),
        unique_sources=unique_sources,
        verified=unique_sources >= min_sources,
        notes=notes,
        top_sources=deduped[:12],
    )


async def run_desktop_autonomy_with_research(
    *,
    client_id: str,
    req: DesktopAutonomyResearchRunRequest,
    session: AsyncSession,
) -> DesktopAutonomyResearchRunResponse:
    queries = _build_queries(req.goal)
    notes: List[str] = []
    gathered: List[ResearchSourceItem] = []

    has_tavily = bool(os.getenv("TAVILY_API_KEY"))
    has_brave = bool(os.getenv("BRAVE_SEARCH_API_KEY"))
    if not has_tavily:
        notes.append("TAVILY_API_KEY not configured; skipped Tavily search.")
    if not has_brave:
        notes.append("BRAVE_SEARCH_API_KEY not configured; Brave fallback may be unavailable.")

    for query in queries:
        tavily_hits = await _search_tavily(query, max_results=req.max_results_per_query)
        if tavily_hits:
            gathered.extend(tavily_hits)
        elif has_tavily:
            notes.append(f"Tavily returned no results for query: {query}")

    deduped_count = len(_dedupe_sources(gathered))
    if deduped_count < req.min_sources and req.include_brave_fallback:
        notes.append("Insufficient Tavily evidence; executing Brave fallback queries.")
        for query in queries[:2]:
            brave_hits = await _search_brave(query, max_results=req.max_results_per_query)
            if brave_hits:
                gathered.extend(brave_hits)
            elif has_brave:
                notes.append(f"Brave returned no results for query: {query}")

    summary = _build_research_summary(
        queries=queries,
        notes=notes,
        sources=gathered,
        min_sources=req.min_sources,
    )

    if not summary.verified and req.require_verified_sources:
        return DesktopAutonomyResearchRunResponse(
            status="blocked",
            detail=(
                "Blocked: insufficient verified sources. "
                "Provide better goal context or configure Tavily/Brave keys."
            ),
            research=summary,
            execution=None,
        )

    execution_request = DesktopAutonomyRunRequest(
        goal=_build_research_goal(req.goal, summary.top_sources),
        max_steps=req.max_steps,
        execution_mode=req.execution_mode,
        start_url=req.start_url,
        agent_id=req.agent_id,
        context={
            **dict(req.context or {}),
            "web_research": {
                "queries": summary.queries,
                "providers_used": summary.providers_used,
                "top_sources": [item.model_dump() for item in summary.top_sources],
                "verified": summary.verified,
            },
        },
        dry_run=req.dry_run,
        wait_for_results=req.wait_for_results,
        step_timeout_seconds=req.step_timeout_seconds,
        enable_recovery=req.enable_recovery,
        stop_on_failure=req.stop_on_failure,
        enable_self_healing=req.enable_self_healing,
        self_heal_max_attempts=req.self_heal_max_attempts,
        quality_min_score=req.quality_min_score,
        enforce_quality_gate=req.enforce_quality_gate,
        dynamic_risk_controls=req.dynamic_risk_controls,
    )
    execution = await run_desktop_autonomy_loop(
        client_id=client_id,
        request=execution_request,
        session=session,
    )
    status = "dry_run" if execution.status == "dry_run" else ("completed" if execution.status == "completed" else "failed")
    return DesktopAutonomyResearchRunResponse(
        status=status,
        detail="Research completed and execution dispatched.",
        research=summary,
        execution=execution,
    )
