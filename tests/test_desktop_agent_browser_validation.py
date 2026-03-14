import pytest
from fastapi import HTTPException

import routes.desktop_agent as desktop_route


def test_validate_browser_goto_allows_allowlisted_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')
    desktop_route._validate_command_args(
        "browser_goto",
        {"url": "https://app.example.com/login"},
    )


def test_validate_browser_goto_rejects_non_allowlisted_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON", '["example.com"]')
    with pytest.raises(HTTPException):
        desktop_route._validate_command_args(
            "browser_goto",
            {"url": "https://evil.com"},
        )


def test_validate_browser_click_requires_target() -> None:
    with pytest.raises(HTTPException):
        desktop_route._validate_command_args("browser_click", {})
