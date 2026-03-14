from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol

import httpx

from .models import EntityInsight, EntityType, MetaAdsMetrics, MetaAdsSnapshot


class MetaAdsClientProtocol(Protocol):
    async def fetch_snapshot(self, account_id: Optional[str] = None) -> MetaAdsSnapshot: ...


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _actions_map(raw: Dict[str, Any]) -> Dict[str, float]:
    items = raw.get("actions") or []
    result: Dict[str, float] = {}
    for item in items:
        token = str(item.get("action_type") or "").strip().lower()
        if token:
            result[token] = _as_float(item.get("value"))
    return result


def _cost_map(raw: Dict[str, Any]) -> Dict[str, float]:
    items = raw.get("cost_per_action_type") or []
    result: Dict[str, float] = {}
    for item in items:
        token = str(item.get("action_type") or "").strip().lower()
        if token:
            result[token] = _as_float(item.get("value"))
    return result


def _pick_metric(mapping: Dict[str, float], *keys: str) -> float:
    for key in keys:
        if key in mapping and mapping[key] > 0:
            return mapping[key]
    return 0.0


def normalize_metrics(raw: Dict[str, Any], downstream: Optional[Dict[str, Any]] = None) -> MetaAdsMetrics:
    actions = _actions_map(raw)
    costs = _cost_map(raw)
    downstream = dict(downstream or {})
    link_clicks = _as_float(
        raw.get("inline_link_clicks")
        or raw.get("outbound_clicks")
        or raw.get("link_clicks")
        or _pick_metric(actions, "link_click", "landing_page_view")
    )
    leads = _pick_metric(actions, "lead", "onsite_conversion.lead_grouped", "omni_lead")
    messages = _pick_metric(
        actions,
        "onsite_conversion.messaging_conversation_started_7d",
        "omni_initiated_contact",
        "messaging_conversation_started_7d",
    )
    bookings = _as_float(downstream.get("bookings") or downstream.get("appointments"))
    revenue = _as_float(downstream.get("revenue"))
    spend = _as_float(
        raw.get("spend")
        or downstream.get("spend")
        or downstream.get("daily_spend_usd")
        or downstream.get("budget_usd")
        or downstream.get("spend_usd")
    )
    roas = _as_float(raw.get("purchase_roas") or raw.get("roas"))
    if not roas and spend > 0 and revenue > 0:
        roas = revenue / spend
    return MetaAdsMetrics(
        spend=spend,
        impressions=_as_float(raw.get("impressions")),
        reach=_as_float(raw.get("reach")),
        frequency=_as_float(raw.get("frequency")),
        clicks=_as_float(raw.get("clicks")),
        link_clicks=link_clicks,
        ctr=_as_float(raw.get("ctr")),
        link_ctr=_as_float(raw.get("inline_link_click_ctr") or raw.get("link_ctr") or downstream.get("ctr_link_pct")),
        cpm=_as_float(raw.get("cpm")),
        cpc=_as_float(raw.get("cpc")),
        leads=leads,
        messaging_conversations=messages,
        bookings=bookings,
        purchases=_pick_metric(actions, "purchase", "omni_purchase"),
        revenue=revenue,
        roas=roas,
        cost_per_lead=_pick_metric(costs, "lead", "onsite_conversion.lead_grouped", "omni_lead"),
        cost_per_message=_pick_metric(
            costs,
            "onsite_conversion.messaging_conversation_started_7d",
            "omni_initiated_contact",
        ),
        cost_per_booking=_as_float(downstream.get("cost_per_booking")),
        reply_rate=_as_float(downstream.get("reply_rate")),
        booked_rate=_as_float(downstream.get("booked_rate")),
        close_rate=_as_float(downstream.get("close_rate")),
        qualified_rate=_as_float(downstream.get("qualified_rate")),
        left_on_read_pct=_as_float(downstream.get("left_on_read_pct")),
        first_response_minutes=_as_float(downstream.get("first_response_minutes")),
        audience_size=_as_float(raw.get("audience_size") or downstream.get("audience_size")),
        budget_change_pct=_as_float(downstream.get("budget_change_pct")),
        sales_change_pct=_as_float(downstream.get("sales_change_pct")),
        ctr_drop_pct=_as_float(downstream.get("ctr_drop_pct")),
    )


def adapt_entity(raw: Dict[str, Any], entity_type: EntityType, downstream: Optional[Dict[str, Any]] = None) -> EntityInsight:
    metrics_source = dict(raw)
    insights = raw.get("insights") or {}
    if isinstance(insights, dict):
        insight_rows = insights.get("data") or []
        if insight_rows:
            metrics_source.update(dict(insight_rows[0] or {}))
    return EntityInsight(
        entity_type=entity_type,
        entity_id=str(raw.get("id") or ""),
        name=str(raw.get("name") or f"{entity_type.value}_{raw.get('id') or 'unknown'}"),
        status=str(raw.get("status") or raw.get("effective_status") or "ACTIVE"),
        objective=str(raw.get("objective") or ""),
        daily_budget=_as_float(raw.get("daily_budget")),
        lifetime_budget=_as_float(raw.get("lifetime_budget")),
        channel=str((downstream or {}).get("channel") or "meta"),
        metadata={
            "bid_strategy": raw.get("bid_strategy"),
            "optimization_goal": raw.get("optimization_goal"),
            "targeting": raw.get("targeting"),
        },
        metrics=normalize_metrics(metrics_source, downstream=downstream),
    )


class FakeMetaAdsClient:
    def __init__(self, snapshot: MetaAdsSnapshot) -> None:
        self._snapshot = snapshot

    async def fetch_snapshot(self, account_id: Optional[str] = None) -> MetaAdsSnapshot:
        return self._snapshot


class MetaAdsApiClient:
    def __init__(
        self,
        *,
        access_token: Optional[str] = None,
        api_version: str = "v22.0",
        base_url: str = "https://graph.facebook.com",
        timeout_seconds: float = 20.0,
    ) -> None:
        self._access_token = access_token or os.getenv("META_ADS_ACCESS_TOKEN", "")
        self._default_account_id = os.getenv("META_AD_ACCOUNT_ID", "")
        self._api_version = api_version
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        token = str(account_id or "").strip()
        if token.lower().startswith("act_"):
            return token[4:]
        return token

    async def _get(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._access_token:
            raise ValueError("META_ADS_ACCESS_TOKEN is required")
        query = dict(params or {})
        query["access_token"] = self._access_token
        url = f"{self._base_url}/{self._api_version}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(url, params=query)
            response.raise_for_status()
            return dict(response.json())

    async def _post(self, path: str, *, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self._access_token:
            raise ValueError("META_ADS_ACCESS_TOKEN is required")
        payload = dict(data)
        payload["access_token"] = self._access_token
        url = f"{self._base_url}/{self._api_version}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(url, data=payload)
            response.raise_for_status()
            return dict(response.json())

    async def fetch_entity(self, entity_id: str, *, fields: str = "id,status,daily_budget") -> Dict[str, Any]:
        return await self._get(entity_id, params={"fields": fields})

    async def pause_entity(self, entity_id: str) -> Dict[str, Any]:
        return await self._post(entity_id, data={"status": "PAUSED"})

    async def update_daily_budget(self, entity_id: str, new_budget_cents: int) -> Dict[str, Any]:
        return await self._post(entity_id, data={"daily_budget": str(new_budget_cents)})

    async def activate_entity(self, entity_id: str) -> Dict[str, Any]:
        return await self._post(entity_id, data={"status": "ACTIVE"})

    async def fetch_entity_insights(self, entity_id: str) -> Dict[str, Any]:
        """Fetch entity with recent insights for ROAS/CPA performance comparison."""
        return await self._get(
            entity_id,
            params={
                "fields": "id,status,daily_budget,insights{spend,purchase_roas,cost_per_action_type}"
            },
        )

    async def fetch_snapshot(self, account_id: Optional[str] = None) -> MetaAdsSnapshot:
        resolved_account_id = self._normalize_account_id(account_id or self._default_account_id)
        if not resolved_account_id:
            raise ValueError("META_AD_ACCOUNT_ID is required when account_id is not provided")
        campaign_fields = ",".join(
            [
                "id",
                "name",
                "status",
                "objective",
                "daily_budget",
                "lifetime_budget",
                "insights{spend,impressions,reach,frequency,clicks,ctr,cpm,cpc,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type}",
            ]
        )
        campaigns_payload = await self._get(
            f"act_{resolved_account_id}/campaigns",
            params={"fields": campaign_fields, "limit": 50},
        )
        campaign_rows = list(campaigns_payload.get("data") or [])
        campaigns = [adapt_entity(row, EntityType.CAMPAIGN) for row in campaign_rows]
        return MetaAdsSnapshot(
            account_id=f"act_{resolved_account_id}",
            account_name=str(campaigns_payload.get("name") or f"act_{resolved_account_id}"),
            objective="lead_generation",
            campaigns=campaigns,
            adsets=[],
            ads=[],
        )
