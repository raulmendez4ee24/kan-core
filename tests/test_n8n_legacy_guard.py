from __future__ import annotations

from pathlib import Path
import re


def test_no_legacy_send_to_n8n_usage_outside_shim() -> None:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    legacy_qualified_call = re.compile(r"\btools\.n8n_client\.send_to_n8n\(")

    for path in root.rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        if path.parts and "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        bad_import = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("from tools.n8n_client import "):
                continue
            imported = stripped.split("import ", 1)[1].strip()
            imported = imported.split("#", 1)[0].strip()
            if imported == "send_to_n8n":
                bad_import = True
                break
            if re.search(r"(^|,)\s*send_to_n8n(\s*,|\s*$)", imported):
                bad_import = True
                break
        if bad_import or legacy_qualified_call.search(text):
            violations.append(str(path.relative_to(root)))

    assert violations == [], (
        "Legacy send_to_n8n() usage is forbidden in Python source: "
        + ", ".join(sorted(violations))
    )


def test_strict_no_send_to_n8n_symbol_outside_shim() -> None:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    legacy_symbol = re.compile(r"\bsend_to_n8n\(")

    for path in root.rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        if path.parts and "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if legacy_symbol.search(text):
            violations.append(str(path.relative_to(root)))

    assert violations == [], (
        "Strict guard: send_to_n8n() symbol must not appear in Python source: "
        + ", ".join(sorted(violations))
    )
