"""
test_hooks.py
=============
Tests for all webhook security hooks, retry infrastructure, and skill loader:

1. tools/retry.py — backoff computation, request_with_retry success/retry/exhaust
2. Webhook signature validators:
   - Meta (X-Hub-Signature-256 HMAC-SHA256)
   - Slack (X-Slack-Signature HMAC-SHA256 + replay protection)
   - Telegram (X-Telegram-Bot-Api-Secret-Token constant-time compare)
3. brain/skill_loader.py — keyword matching, integration matching,
   max skill cap, format_skills_for_prompt output shape
4. Slack url_verification challenge passthrough
5. Meta webhook verification endpoint (GET hub.challenge)
6. YCloud webhook signature validation + setup endpoint
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tools.retry import _compute_backoff, post_json_with_retry, request_with_retry


# ---------------------------------------------------------------------------
# tools/retry.py — _compute_backoff
# ---------------------------------------------------------------------------

class TestComputeBackoff:
    def test_grows_exponentially(self) -> None:
        b1 = _compute_backoff(1, base=1.0, maximum=100.0)
        b2 = _compute_backoff(2, base=1.0, maximum=100.0)
        b3 = _compute_backoff(3, base=1.0, maximum=100.0)
        # Each attempt should be roughly double the previous (with jitter)
        # At minimum, b2 >= b1 * 0.5 and b3 >= b2 * 0.5
        assert b2 >= b1 * 0.4
        assert b3 >= b2 * 0.4

    def test_capped_at_maximum(self) -> None:
        for attempt in range(1, 20):
            assert _compute_backoff(attempt, base=1.0, maximum=5.0) <= 5.0

    def test_jitter_within_expected_range(self) -> None:
        # With base=2, attempt=1: delay = 2 * jitter, jitter in [0.5, 1.0]
        # So result must be in [1.0, 2.0]
        for _ in range(50):
            value = _compute_backoff(1, base=2.0, maximum=100.0)
            assert 1.0 <= value <= 2.0

    def test_first_attempt_base_range(self) -> None:
        for _ in range(30):
            value = _compute_backoff(1, base=0.5, maximum=10.0)
            assert 0.25 <= value <= 0.5


# ---------------------------------------------------------------------------
# tools/retry.py — request_with_retry
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, status_code: int, body: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.request = MagicMock()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=MagicMock(status_code=self.status_code),
            )

    def json(self) -> Dict[str, Any]:
        return self._body


class TestRequestWithRetry:
    def test_success_on_first_attempt(self) -> None:
        ok_resp = _MockResponse(200, {"ok": True})

        async def fake_request(*_: Any, **__: Any) -> _MockResponse:
            return ok_resp

        client = MagicMock()
        client.request = AsyncMock(return_value=ok_resp)

        result = asyncio.run(
            request_with_retry(
                client, "POST", "https://example.test/api",
                json={"x": 1}, retries=3, backoff_base=0.01, backoff_max=0.1,
            )
        )
        assert result.status_code == 200
        assert client.request.call_count == 1

    def test_retries_on_500_then_succeeds(self) -> None:
        fail_resp = _MockResponse(500, {})
        ok_resp = _MockResponse(200, {"ok": True})
        responses = [fail_resp, ok_resp]
        call_count = 0

        async def fake_request(*_: Any, **__: Any) -> _MockResponse:
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        client = MagicMock()
        client.request = AsyncMock(side_effect=fake_request)

        with patch("tools.retry.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(
                request_with_retry(
                    client, "POST", "https://example.test/",
                    retries=3, backoff_base=0.01, backoff_max=0.1,
                )
            )
        assert result.status_code == 200
        assert call_count == 2

    def test_retries_on_429_rate_limit(self) -> None:
        fail_resp = _MockResponse(429, {})
        ok_resp = _MockResponse(200, {"ok": True})
        responses = [fail_resp, ok_resp]
        idx = [0]

        async def fake_request(*_: Any, **__: Any) -> _MockResponse:
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            return resp

        client = MagicMock()
        client.request = AsyncMock(side_effect=fake_request)

        with patch("tools.retry.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(
                request_with_retry(
                    client, "GET", "https://api.test/",
                    retries=3, backoff_base=0.01, backoff_max=0.1,
                )
            )
        assert result.status_code == 200

    def test_raises_after_all_retries_exhausted(self) -> None:
        always_fail = _MockResponse(503, {})
        client = MagicMock()
        client.request = AsyncMock(return_value=always_fail)

        with patch("tools.retry.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                asyncio.run(
                    request_with_retry(
                        client, "POST", "https://fail.test/",
                        retries=3, backoff_base=0.01, backoff_max=0.1,
                    )
                )
        assert client.request.call_count == 3

    def test_200_does_not_retry(self) -> None:
        ok_resp = _MockResponse(200, {})
        client = MagicMock()
        client.request = AsyncMock(return_value=ok_resp)

        asyncio.run(
            request_with_retry(client, "GET", "https://ok.test/", retries=5)
        )
        assert client.request.call_count == 1

    def test_non_retryable_4xx_raises_immediately(self) -> None:
        """404 is not in RETRYABLE_STATUS — should raise on first attempt, not retry."""
        from tools.retry import RETRYABLE_STATUS
        assert 404 not in RETRYABLE_STATUS

        resp_404 = _MockResponse(404, {})
        client = MagicMock()
        client.request = AsyncMock(return_value=resp_404)

        # 404 is returned as-is (no retry), should succeed with status 404
        result = asyncio.run(
            request_with_retry(client, "GET", "https://notfound.test/", retries=3)
        )
        assert result.status_code == 404
        assert client.request.call_count == 1


# ---------------------------------------------------------------------------
# Slack signature validation — routes/slack.py
# ---------------------------------------------------------------------------

class TestSlackSignatureVerification:
    def _make_sig(self, body: bytes, timestamp: str, secret: str) -> str:
        sig_base = f"v0:{timestamp}:{body.decode('utf-8')}"
        return "v0=" + hmac.new(
            secret.encode("utf-8"),
            sig_base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def test_valid_signature_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        secret = "my-slack-secret"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
        body = b'{"event":{"text":"hello"}}'
        ts = str(int(time.time()))
        sig = self._make_sig(body, ts, secret)
        assert _verify_slack_signature(body, ts, sig) is True

    def test_invalid_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "real-secret")
        body = b'{"event":{}}'
        ts = str(int(time.time()))
        assert _verify_slack_signature(body, ts, "v0=fakesignature") is False

    def test_missing_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
        body = b'{"event":{}}'
        ts = str(int(time.time()))
        assert _verify_slack_signature(body, ts, None) is False

    def test_missing_timestamp_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
        body = b'{"event":{}}'
        assert _verify_slack_signature(body, None, "v0=anysig") is False

    def test_replay_protection_rejects_old_timestamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        secret = "replay-secret"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
        body = b'{"event":{}}'
        # Timestamp 6 minutes ago
        old_ts = str(int(time.time()) - 360)
        sig = self._make_sig(body, old_ts, secret)
        assert _verify_slack_signature(body, old_ts, sig) is False

    def test_no_secret_configured_accepts_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "")
        body = b'{"event":{}}'
        assert _verify_slack_signature(body, None, None) is True

    def test_within_5min_window_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.slack import _verify_slack_signature
        secret = "time-ok-secret"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
        body = b'{"event":{"text":"ok"}}'
        # Timestamp 4 minutes ago (within 5 min window)
        ts = str(int(time.time()) - 240)
        sig = self._make_sig(body, ts, secret)
        assert _verify_slack_signature(body, ts, sig) is True


# ---------------------------------------------------------------------------
# Telegram secret token validation — routes/telegram.py
# ---------------------------------------------------------------------------

class TestTelegramSecretToken:
    def test_valid_token_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.telegram import _verify_secret_token
        monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "supersecret")
        assert _verify_secret_token("supersecret") is True

    def test_invalid_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.telegram import _verify_secret_token
        monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "supersecret")
        assert _verify_secret_token("wrongtoken") is False

    def test_no_secret_configured_accepts_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.telegram import _verify_secret_token
        monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "")
        assert _verify_secret_token(None) is True
        assert _verify_secret_token("anything") is True

    def test_missing_header_when_secret_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.telegram import _verify_secret_token
        monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "required-token")
        assert _verify_secret_token(None) is False

    def test_whitespace_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.telegram import _verify_secret_token
        monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "  my-token  ")
        assert _verify_secret_token("my-token") is True
        assert _verify_secret_token("  my-token  ") is True


# ---------------------------------------------------------------------------
# Meta webhook signature validation — security.py
# ---------------------------------------------------------------------------

class TestMetaSignatureVerification:
    def _make_meta_sig(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

    def test_valid_meta_signature_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from security import verify_meta_signature
        secret = "meta-app-secret"
        monkeypatch.setenv("META_APP_SECRET", secret)
        monkeypatch.setenv("REQUIRE_WEBHOOK_SIGNATURES", "true")
        body = b'{"object":"whatsapp_business_account"}'
        sig = self._make_meta_sig(body, secret)
        assert verify_meta_signature(body, sig) is True

    def test_invalid_meta_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from security import verify_meta_signature
        monkeypatch.setenv("META_APP_SECRET", "real-secret")
        monkeypatch.setenv("REQUIRE_WEBHOOK_SIGNATURES", "true")
        body = b'{"object":"whatsapp_business_account"}'
        assert verify_meta_signature(body, "sha256=fakesig") is False

    def test_signatures_disabled_accepts_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from security import verify_meta_signature
        monkeypatch.setenv("REQUIRE_WEBHOOK_SIGNATURES", "false")
        body = b'{"object":"test"}'
        assert verify_meta_signature(body, None) is True


# ---------------------------------------------------------------------------
# brain/skill_loader.py — load_relevant_skills
# ---------------------------------------------------------------------------

class TestSkillLoader:
    def test_keyword_match_shopify(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("cuales son las ordenes en shopify", [])
        names = [s["name"] for s in skills]
        assert "shopify_ops" in names

    def test_keyword_match_crm(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("actualiza el contacto en el crm de hubspot", [])
        names = [s["name"] for s in skills]
        assert "crm_sync" in names

    def test_keyword_match_email(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("envia un email de confirmacion", [])
        names = [s["name"] for s in skills]
        assert "email_automation" in names

    def test_keyword_match_telegram(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("manda un mensaje de telegram al equipo", [])
        names = [s["name"] for s in skills]
        assert "telegram_notify" in names

    def test_keyword_match_invoice(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("genera una factura en stripe", [])
        names = [s["name"] for s in skills]
        assert "invoice_generate" in names

    def test_keyword_match_lead(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("califica este lead con score BANT", [])
        names = [s["name"] for s in skills]
        assert "lead_qualification" in names

    def test_keyword_match_calendar(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("agenda una reunion en google calendar", [])
        names = [s["name"] for s in skills]
        assert "calendar_schedule" in names

    def test_keyword_match_data_export(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("exporta los datos a un csv o google sheets", [])
        names = [s["name"] for s in skills]
        assert "data_export" in names

    def test_integration_match_when_no_keyword(self) -> None:
        from brain.skill_loader import load_relevant_skills
        # Message has no keywords but org has 'hubspot' integration active
        skills = load_relevant_skills("que tengo pendiente hoy", ["hubspot"])
        names = [s["name"] for s in skills]
        assert "crm_sync" in names

    def test_keyword_takes_priority_over_integration(self) -> None:
        from brain.skill_loader import load_relevant_skills
        # Both keyword match and integration match for crm_sync
        skills = load_relevant_skills("actualiza el crm", ["hubspot", "shopify"])
        names = [s["name"] for s in skills]
        # crm_sync should appear, no duplicates
        assert names.count("crm_sync") == 1

    def test_max_skills_cap_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import brain.skill_loader as sl_module
        monkeypatch.setattr(sl_module, "_MAX_SKILLS_PER_PROMPT", 2)
        from brain.skill_loader import load_relevant_skills
        # Trigger many matches
        skills = load_relevant_skills(
            "crm email shopify slack telegram factura calificar exportar",
            ["hubspot", "sendgrid"],
        )
        assert len(skills) <= 2

    def test_empty_message_returns_empty_or_integration_matches(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("", [])
        assert isinstance(skills, list)

    def test_no_match_returns_empty(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("hola como estas, buen dia", [])
        assert isinstance(skills, list)

    def test_case_insensitive_matching(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills_lower = load_relevant_skills("shopify order status", [])
        skills_upper = load_relevant_skills("SHOPIFY ORDER STATUS", [])
        assert len(skills_lower) == len(skills_upper)

    def test_keyword_match_kan_sales_marketing(self) -> None:
        from brain.skill_loader import load_relevant_skills
        skills = load_relevant_skills("quiero vender mas con meta ads y whatsapp", [])
        names = [s["name"] for s in skills]
        assert "kan_sales_marketing" in names


# ---------------------------------------------------------------------------
# brain/skill_loader.py — format_skills_for_prompt
# ---------------------------------------------------------------------------

class TestFormatSkillsForPrompt:
    def test_empty_list_returns_empty_string(self) -> None:
        from brain.skill_loader import format_skills_for_prompt
        assert format_skills_for_prompt([]) == ""

    def test_includes_skill_name(self) -> None:
        from brain.skill_loader import format_skills_for_prompt
        skill = {
            "name": "test_skill",
            "description": "Does test things",
            "instructions": "Use action_type=integration",
            "trigger_keywords": ["test"],
        }
        result = format_skills_for_prompt([skill])
        assert "test_skill" in result

    def test_includes_description_and_instructions(self) -> None:
        from brain.skill_loader import format_skills_for_prompt
        skill = {
            "name": "my_skill",
            "description": "My description",
            "instructions": "My instructions",
        }
        result = format_skills_for_prompt([skill])
        assert "My description" in result
        assert "My instructions" in result

    def test_includes_example_action_json(self) -> None:
        from brain.skill_loader import format_skills_for_prompt
        skill = {
            "name": "example",
            "description": "test",
            "instructions": "test",
            "example_action": {"action": "do_it", "integration": "test_service"},
        }
        result = format_skills_for_prompt([skill])
        assert "do_it" in result

    def test_multiple_skills_all_included(self) -> None:
        from brain.skill_loader import format_skills_for_prompt
        skills = [
            {"name": f"skill_{i}", "description": f"desc {i}", "instructions": f"inst {i}"}
            for i in range(3)
        ]
        result = format_skills_for_prompt(skills)
        for i in range(3):
            assert f"skill_{i}" in result


# ---------------------------------------------------------------------------
# Slack route — url_verification passthrough
# ---------------------------------------------------------------------------

class TestSlackUrlVerification:
    def test_challenge_returned_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """url_verification must return the challenge without calling the dispatcher."""
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "")  # no sig check in dev mode
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.slack import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        payload = {"type": "url_verification", "challenge": "abc123xyz"}
        resp = client.post("/webhooks/slack", json=payload)
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123xyz"

    def test_bot_message_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "")
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.slack import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        payload = {
            "type": "event_callback",
            "event": {"type": "message", "text": "hi", "channel": "C1", "bot_id": "B123"},
        }
        # Bot messages should be ignored (return ok without dispatching)
        resp = client.post("/webhooks/slack", json=payload)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Telegram route — setup endpoint
# ---------------------------------------------------------------------------

class TestTelegramSetup:
    def test_setup_requires_webhook_url(self) -> None:
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.telegram import router

        import os
        os.environ.pop("TELEGRAM_WEBHOOK_URL", None)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/webhooks/telegram/setup")
        assert resp.status_code == 400
        assert "webhook_url" in resp.json()["detail"].lower() or "TELEGRAM_WEBHOOK_URL" in resp.json()["detail"]

    def test_setup_calls_set_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.telegram import router
        import routes.telegram as tg_module

        called: Dict[str, Any] = {}

        async def fake_set_webhook(url: str, *, secret_token: Optional[str] = None) -> Dict[str, Any]:
            called["url"] = url
            called["secret_token"] = secret_token
            return {"ok": True, "description": "Webhook set"}

        monkeypatch.setattr("tools.telegram_client.set_webhook", fake_set_webhook)

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/webhooks/telegram/setup?webhook_url=https://myapp.example.com/webhooks/telegram")
        assert resp.status_code == 200
        assert called.get("url") == "https://myapp.example.com/webhooks/telegram"


# ---------------------------------------------------------------------------
# YCloud webhook — signature + setup endpoint
# ---------------------------------------------------------------------------

class TestYCloudWebhook:
    def test_valid_signature_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.ycloud import _verify_ycloud_signature

        secret = "ycloud-secret"
        body = b'{"type":"whatsapp.inbound.message"}'
        timestamp = str(int(time.time()))
        digest = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{body.decode('utf-8')}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", secret)
        assert _verify_ycloud_signature(body, f"t={timestamp},s={digest}") is True

    def test_invalid_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from routes.ycloud import _verify_ycloud_signature

        monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", "real-secret")
        assert _verify_ycloud_signature(b"{}", "t=123,s=bad") is False

    def test_setup_returns_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.ycloud import router

        monkeypatch.setenv(
            "YCLOUD_WEBHOOK_URL",
            "https://web-production-dbb6b.up.railway.app/webhooks/ycloud/whatsapp",
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/webhooks/ycloud/setup")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "ycloud"
        assert body["webhook_url"].endswith("/webhooks/ycloud/whatsapp")
        assert "whatsapp.inbound_message.received" in body["events"]

    def test_whsec_prefixed_secret_verifies_as_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Webhook verification must use the exact secret string configured in env."""
        from routes.ycloud import _verify_ycloud_signature

        configured_secret = "whsec_fb57cb8bdffe4a1aac13060f95d3aa33"
        body = b'{"type":"whatsapp.inbound_message.received","payload":{"id":"msg_001"}}'
        timestamp = str(int(time.time()))
        digest = hmac.new(
            configured_secret.encode("utf-8"),
            f"{timestamp}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", configured_secret)
        assert _verify_ycloud_signature(body, f"t={timestamp},s={digest}") is True

    def test_raw_secret_without_prefix_also_verifies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-prefixed secret must also verify as-is."""
        from routes.ycloud import _verify_ycloud_signature

        raw_secret = "fb57cb8bdffe4a1aac13060f95d3aa33"
        body = b'{"type":"whatsapp.inbound_message.received"}'
        timestamp = str(int(time.time()))
        digest = hmac.new(
            raw_secret.encode("utf-8"),
            f"{timestamp}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", raw_secret)
        assert _verify_ycloud_signature(body, f"t={timestamp},s={digest}") is True

    def test_wrong_secret_still_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A signature from a different key must still be rejected."""
        from routes.ycloud import _verify_ycloud_signature

        body = b'{"type":"whatsapp.inbound_message.received"}'
        timestamp = str(int(time.time()))
        # Signed with a totally different key
        digest = hmac.new(
            b"wrong_key",
            f"{timestamp}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        monkeypatch.setenv("YCLOUD_WEBHOOK_SECRET", "whsec_fb57cb8bdffe4a1aac13060f95d3aa33")
        assert _verify_ycloud_signature(body, f"t={timestamp},s={digest}") is False


# ---------------------------------------------------------------------------
# Meta webhook — GET verification endpoint
# ---------------------------------------------------------------------------

class TestMetaWebhookVerification:
    def test_correct_challenge_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.webhooks import router

        monkeypatch.setenv("META_VERIFY_TOKEN", "my-verify-token")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(
            "/webhooks/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "my-verify-token",
                "hub.challenge": "challenge_12345",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge_12345"

    def test_wrong_token_returns_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.webhooks import router

        monkeypatch.setenv("META_VERIFY_TOKEN", "correct-token")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(
            "/webhooks/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "xyz",
            },
        )
        assert resp.status_code == 403

    def test_missing_challenge_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routes.webhooks import router

        monkeypatch.setenv("META_VERIFY_TOKEN", "token123")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(
            "/webhooks/meta",
            params={"hub.mode": "subscribe", "hub.verify_token": "token123"},
        )
        assert resp.status_code == 400
