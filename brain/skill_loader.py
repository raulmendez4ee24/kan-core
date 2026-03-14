from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_MAX_SKILLS_PER_PROMPT = 3
_LEGACY_SKILLS = [
    {
        "name": "kan_sales_marketing",
        "description": "Sell KAN Logic offers with first-person Spanish responses, clear business framing and movement toward qualification or meeting requests.",
        "instructions": (
            "Cuando hables de chatbots, agentes, automatizacion, paginas web, Meta Ads o ventas, "
            "responde en primera persona, en espanol, conecta el problema con dinero/tiempo/seguimiento y "
            "lleva la conversacion hacia diagnostico, recomendacion o reunion. KAN Logic vende chatbots, "
            "agentes de voz, agentes autonomos, paginas web, planes mensuales, combos y add-ons."
        ),
        "trigger_keywords": [
            "chatbot", "chatbots", "agente", "agentes", "automatizacion", "automatización",
            "whatsapp", "meta ads", "facebook ads", "instagram ads", "marketing", "ventas",
            "pagina web", "página web", "landing", "sitio web", "demo", "precio", "cotizacion", "cotización",
            "implementacion", "implementación",
        ],
        "integrations": ["ycloud", "whatsapp"],
    },
    {
        "name": "shopify_ops",
        "description": "Operate Shopify orders and commerce flows.",
        "instructions": "Use integration actions for Shopify order and catalog tasks.",
        "trigger_keywords": ["shopify", "orden", "order"],
        "integrations": ["shopify"],
    },
    {
        "name": "crm_sync",
        "description": "Work with CRM updates and contact records.",
        "instructions": "Prefer CRM integrations when touching leads or contacts.",
        "trigger_keywords": ["crm", "contacto", "contact", "hubspot", "lead"],
        "integrations": ["hubspot", "salesforce", "pipedrive"],
    },
    {
        "name": "email_automation",
        "description": "Send or automate email workflows.",
        "instructions": "Use messaging or integration actions for email delivery.",
        "trigger_keywords": ["email", "correo", "mail"],
        "integrations": ["sendgrid", "mailgun", "gmail"],
    },
    {
        "name": "telegram_notify",
        "description": "Notify teams through Telegram.",
        "instructions": "Use telegram-capable integrations for team notifications.",
        "trigger_keywords": ["telegram"],
        "integrations": ["telegram"],
    },
    {
        "name": "invoice_generate",
        "description": "Generate invoices or billing artifacts.",
        "instructions": "Use billing integrations when handling invoices.",
        "trigger_keywords": ["factura", "invoice", "stripe"],
        "integrations": ["stripe"],
    },
    {
        "name": "lead_qualification",
        "description": "Score and qualify leads.",
        "instructions": "Use CRM data and lead scoring heuristics.",
        "trigger_keywords": ["califica", "qualify", "lead", "bant"],
        "integrations": ["hubspot", "salesforce"],
    },
    {
        "name": "calendar_schedule",
        "description": "Schedule meetings or events.",
        "instructions": "Use calendar tools for meeting scheduling.",
        "trigger_keywords": ["calendar", "calendario", "agenda", "reunion"],
        "integrations": ["google_calendar", "outlook"],
    },
    {
        "name": "data_export",
        "description": "Export business data to files or sheets.",
        "instructions": "Use export or spreadsheet actions for data extraction.",
        "trigger_keywords": ["exporta", "export", "csv", "sheets", "google sheets"],
        "integrations": ["sheets", "google_sheets"],
    },
]


def _skills_root() -> Path:
    configured = str(os.getenv("AGENT_SKILLS_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "skills"


def _skill_path(skill_name: str) -> Path:
    token = str(skill_name or "").strip().lower()
    return _skills_root() / token / "SKILL.md"


def _summarize(content: str) -> str:
    lines = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
        if len(lines) >= 3:
            break
    return " ".join(lines)[:500]


def list_skill_names() -> List[str]:
    root = _skills_root()
    if not root.exists():
        return []
    items: List[str] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            items.append(child.name)
    return items


def load_skill(skill_name: str) -> Optional[Dict[str, Any]]:
    path = _skill_path(skill_name)
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    return {
        "name": path.parent.name,
        "path": str(path),
        "content": content,
        "summary": _summarize(content),
    }


def load_relevant_skills(message: str, active_integrations: List[str]) -> List[Dict[str, Any]]:
    lowered = str(message or "").strip().lower()
    integrations = {str(item).strip().lower() for item in (active_integrations or []) if str(item).strip()}
    scored: List[Dict[str, Any]] = []
    for skill in _LEGACY_SKILLS:
        keywords = [str(item).lower() for item in skill.get("trigger_keywords", [])]
        integration_names = {str(item).lower() for item in skill.get("integrations", [])}
        keyword_hit = any(keyword in lowered for keyword in keywords) if lowered else False
        integration_hit = bool(integrations & integration_names)
        if not keyword_hit and not integration_hit:
            continue
        score = 2 if keyword_hit else 1
        scored.append({**skill, "_score": score})
    scored.sort(key=lambda item: (-int(item["_score"]), str(item["name"])))
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in scored:
        name = str(item["name"])
        if name in seen:
            continue
        seen.add(name)
        item = dict(item)
        item.pop("_score", None)
        deduped.append(item)
        if len(deduped) >= max(1, int(_MAX_SKILLS_PER_PROMPT)):
            break
    return deduped


def format_skills_for_prompt(skills: List[Dict[str, Any]]) -> str:
    if not skills:
        return ""
    parts: List[str] = []
    for item in skills:
        parts.append(f"[{item.get('name', 'skill')}]")
        if item.get("description"):
            parts.append(str(item["description"]))
        if item.get("instructions"):
            parts.append(str(item["instructions"]))
        if item.get("example_action"):
            parts.append(str(item["example_action"]))
    return "\n".join(parts)
