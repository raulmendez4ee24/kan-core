from __future__ import annotations

import asyncio

import pytest

from brain.meta_ads_operator.client import MetaAdsApiClient


def test_normalize_account_id_strips_act_prefix() -> None:
    assert MetaAdsApiClient._normalize_account_id("act_2000310323604893") == "2000310323604893"
    assert MetaAdsApiClient._normalize_account_id("2000310323604893") == "2000310323604893"


def test_fetch_snapshot_uses_default_account_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_2000310323604893")
    client = MetaAdsApiClient()
    captured: dict[str, object] = {}

    async def fake_get(path: str, *, params: dict[str, object] | None = None) -> dict[str, object]:
        captured["path"] = path
        captured["params"] = params or {}
        return {"data": []}

    client._get = fake_get  # type: ignore[method-assign]
    snapshot = asyncio.run(client.fetch_snapshot())

    assert captured["path"] == "act_2000310323604893/campaigns"
    assert snapshot.account_id == "act_2000310323604893"


def test_fetch_snapshot_raises_without_account_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    client = MetaAdsApiClient()

    with pytest.raises(ValueError, match="META_AD_ACCOUNT_ID is required"):
        asyncio.run(client.fetch_snapshot())
