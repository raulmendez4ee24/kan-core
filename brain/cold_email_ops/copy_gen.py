"""Fase 7: Copy gen — build personalization, subject, and body from lead + campaign."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def run(
    data: Dict[str, List[Dict[str, Any]]],
    log: List[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """Generate practical outreach copy from campaign settings for downstream senders."""
    leads = data.get("leads", [])
    campaign = data.get("campaign", [])
    cfg = campaign[0] if campaign else {}

    personalization_rules = ""
    if cfg:
        personalization_rules = str(cfg.get("personalization_rules") or "").strip()
    offer = str(cfg.get("offer") or "").strip()
    send_window = str(cfg.get("send_window_localtime") or "").strip()
    sequence_total = max(1, _parse_int(cfg.get("sequence"), default=1))

    for row in leads:
        company = (row.get("company") or "").strip()
        first_name = (row.get("first_name") or "").strip()
        segment = (row.get("segment") or "").strip()
        current_snippet = str(row.get("personalization_snippet") or "").strip()
        step = max(1, _parse_int(row.get("sequence_step"), default=_extract_step_from_notes(row.get("notes")) or 1))
        total = max(step, _parse_int(row.get("sequence_total"), default=sequence_total))

        snippet = current_snippet or _fallback_snippet(company, first_name, segment)
        if personalization_rules and personalization_rules.lower() not in snippet.lower():
            snippet = f"{snippet} {personalization_rules}".strip()
        row["personalization_snippet"] = snippet[:500]
        row["subject"] = _build_subject(company, offer, step)
        row["body"] = _build_body(
            first_name=first_name,
            company=company,
            snippet=row["personalization_snippet"],
            offer=offer,
            step=step,
            total=total,
            send_window=send_window,
        )

    log.append("Copy gen: personalization_snippet, subject and body set for all leads")
    return data, log


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(float(text))
    except (ValueError, TypeError):
        match = re.search(r"(\d+)", text)
        if match:
            return int(match.group(1))
    return default


def _extract_step_from_notes(notes: Any) -> int | None:
    text = str(notes or "")
    match = re.search(r"\[ce_meta[^\]]*step=(\d+)/(\d+)[^\]]*\]", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (ValueError, TypeError):
        return None


def _fallback_snippet(company: str, first_name: str, segment: str) -> str:
    parts = []
    if company:
        parts.append(f"Company: {company}.")
    if first_name:
        parts.append(f"First name: {first_name}.")
    if segment:
        parts.append(f"Segment: {segment}.")
    return " ".join(parts).strip() or "Relevant business in our target segment."


def _build_subject(company: str, offer: str, step: int) -> str:
    if step <= 1:
        if offer and company:
            return f"Quick idea for {company}: {offer}"
        if company:
            return f"Quick idea for {company}"
        if offer:
            return f"Quick idea: {offer}"
        return "Quick idea"
    if offer:
        return f"Following up on {offer}"
    if company:
        return f"Quick follow-up for {company}"
    return "Quick follow-up"


def _build_body(
    first_name: str,
    company: str,
    snippet: str,
    offer: str,
    step: int,
    total: int,
    send_window: str,
) -> str:
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    company_ref = company or "your team"
    offer_line = offer or f"a practical outbound idea that could fit {company_ref}"

    if step <= 1:
        lines = [
            greeting,
            "",
            snippet,
            "",
            f"I'm reaching out because I have {offer_line}.",
            f"If this is relevant, I can send a short version tailored to {company_ref}.",
        ]
    else:
        lines = [
            greeting,
            "",
            f"Quick follow-up ({step}/{total}) on my previous note.",
            snippet,
            "",
            f"The core idea is still {offer_line}.",
            f"If now is not a fit, I can close the loop. If it is, I can send the next step for {company_ref}.",
        ]

    if send_window:
        lines.extend(["", f"Preferred send window: {send_window}."])
    lines.extend(["", "Best,"])
    return "\n".join(lines)[:4000]
