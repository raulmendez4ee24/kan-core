import base64
import hashlib
import hmac

import pytest

from security import verify_meta_signature, verify_shopify_hmac


def test_meta_signature_is_required_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_WEBHOOK_SIGNATURES", raising=False)

    assert verify_meta_signature(b"{}", None) is False


def test_meta_signature_can_be_relaxed_for_local_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    monkeypatch.setenv("REQUIRE_WEBHOOK_SIGNATURES", "false")

    assert verify_meta_signature(b"{}", None) is True


def test_meta_signature_verification_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"entry":[]}'
    secret = "meta-secret"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    monkeypatch.setenv("META_APP_SECRET", secret)

    assert verify_meta_signature(body, expected) is True
    assert verify_meta_signature(body, "sha256=bad") is False


def test_shopify_signature_verification_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"id":123}'
    secret = "shopify-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()

    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", secret)

    assert verify_shopify_hmac(body, expected) is True
    assert verify_shopify_hmac(body, "bad") is False
