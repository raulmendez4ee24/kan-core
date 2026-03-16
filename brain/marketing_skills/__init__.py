# brain/marketing_skills/__init__.py
# Loader for marketing skill context injected into LLM system prompts.
from __future__ import annotations

import functools
from pathlib import Path

_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=None)
def load_skill(name: str) -> str:
    """Return the full text of a skill markdown file, or '' if missing."""
    path = _DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_skills(*names: str) -> str:
    """Concatenate multiple skills into a single context block."""
    parts: list[str] = []
    for name in names:
        content = load_skill(name)
        if content:
            parts.append(f"=== MARKETING SKILL: {name.upper().replace('_', '-')} ===\n\n{content}")
    return "\n\n" + ("\n\n---\n\n".join(parts)) if parts else ""
