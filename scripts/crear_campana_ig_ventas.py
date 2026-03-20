#!/usr/bin/env python3
"""Crear campaña de ventas en Instagram vía Meta Marketing API.

Estructura:
  Campaign (OUTCOME_SALES) → Ad Set (CONVERSATIONS/WhatsApp) → Creative (page post) → Ad

Config:
  - Presupuesto: $100 MXN/día (configurable via DAILY_BUDGET_MXN)
  - Placement: Auto (Advantage+)
  - Targeting: México, 25-65, Advantage+ Audience
  - Optimización: Conversaciones WhatsApp
  - Status: PAUSED (activar desde Meta Ads Manager)

Nota: La app de Meta debe estar en modo Live para crear creatives nuevos.
      Como workaround, usamos posts existentes de la página como creative.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_ENV_PATH = os.getenv("ENV_PATH", "/Users/raulaldairmendezalvarez/dev/chatbotn8n/.env")
_SECRETS_PATH = os.getenv("SECRETS_PATH", "/Users/raulaldairmendezalvarez/dev/chatbotn8n/.env.secrets")

GRAPH_BASE = "https://graph.facebook.com/v22.0"
PAGE_ID = "1066904776499189"
WHATSAPP_NUMBER = "5213326617755"


def _load_env() -> dict[str, str]:
    config = dotenv_values(_ENV_PATH)
    secrets = dotenv_values(_SECRETS_PATH)
    config.update(secrets)
    return config


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


async def _post(client: httpx.AsyncClient, path: str, token: str, data: dict) -> dict:
    payload = {**data, "access_token": token}
    url = f"{GRAPH_BASE}/{path.lstrip('/')}"
    resp = await client.post(url, data=payload)
    result = resp.json()
    if resp.status_code != 200 or "error" in result:
        err = result.get("error", {})
        print(f"  ERROR {resp.status_code}: {err.get('error_user_msg') or err.get('message', 'unknown')}")
    return result


async def _get(client: httpx.AsyncClient, path: str, token: str, params: dict | None = None) -> dict:
    url = f"{GRAPH_BASE}/{path.lstrip('/')}"
    resp = await client.get(url, params={"access_token": token, **(params or {})})
    return resp.json()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    env = _load_env()
    token = env.get("META_ADS_ACCESS_TOKEN", "")
    account_id = env.get("META_AD_ACCOUNT_ID", "").replace("act_", "")
    daily_budget_mxn = int(os.getenv("DAILY_BUDGET_MXN", "100"))

    if not token or not account_id:
        print("ERROR: META_ADS_ACCESS_TOKEN y META_AD_ACCOUNT_ID son requeridos")
        sys.exit(1)

    act = f"act_{account_id}"
    daily_budget_centavos = str(daily_budget_mxn * 100)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get the best page post to use as creative
        pages_resp = await _get(client, "me/accounts", token, {"fields": "id,access_token"})
        page_token = None
        for page in pages_resp.get("data", []):
            if page.get("id") == PAGE_ID:
                page_token = page.get("access_token")
                break

        if not page_token:
            print("ERROR: No se encontró el token de la página")
            sys.exit(1)

        posts_resp = await _get(client, f"{PAGE_ID}/posts", page_token, {
            "fields": "id,message,created_time", "limit": "5",
        })
        page_posts = posts_resp.get("data", [])
        if not page_posts:
            print("ERROR: La página no tiene posts para usar como creative")
            sys.exit(1)

        # Pick first post with a message
        page_post_id = None
        for post in page_posts:
            if post.get("message"):
                page_post_id = post["id"]
                print(f"  Post seleccionado: {post['message'][:60]}...")
                break
        if not page_post_id:
            page_post_id = page_posts[0]["id"]

        # ---------------------------------------------------------------
        # Step 1: Create Campaign
        # ---------------------------------------------------------------
        print("\n=== STEP 1: Campaign ===")
        campaign = await _post(client, f"{act}/campaigns", token, {
            "name": "KAN Ventas IG - WhatsApp",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": "[]",
            "daily_budget": daily_budget_centavos,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "is_adset_budget_sharing_enabled": "false",
        })
        campaign_id = campaign.get("id")
        if not campaign_id:
            return
        print(f"  Campaign: {campaign_id}")

        # ---------------------------------------------------------------
        # Step 2: Create Ad Set
        # ---------------------------------------------------------------
        print("\n=== STEP 2: Ad Set ===")
        targeting = json.dumps({
            "age_min": 25,
            "age_max": 65,
            "geo_locations": {"countries": ["MX"], "location_types": ["home", "recent"]},
            "targeting_automation": {
                "advantage_audience": 1,
                "individual_setting": {"age": 1, "gender": 1},
            },
        })
        promoted_object = json.dumps({
            "page_id": PAGE_ID,
            "whatsapp_phone_number": WHATSAPP_NUMBER,
        })
        adset = await _post(client, f"{act}/adsets", token, {
            "name": "KAN IG Ventas - MX 25-65 WhatsApp",
            "campaign_id": campaign_id,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "CONVERSATIONS",
            "destination_type": "WHATSAPP",
            "targeting": targeting,
            "promoted_object": promoted_object,
            "status": "PAUSED",
        })
        adset_id = adset.get("id")
        if not adset_id:
            await _post(client, campaign_id, token, {"status": "DELETED"})
            return
        print(f"  Ad Set: {adset_id}")

        # ---------------------------------------------------------------
        # Step 3: Create Ad Creative
        # ---------------------------------------------------------------
        print("\n=== STEP 3: Creative ===")
        creative = await _post(client, f"{act}/adcreatives", token, {
            "name": "KAN Ventas - Post de Pagina",
            "object_story_id": page_post_id,
        })
        creative_id = creative.get("id")
        if not creative_id:
            await _post(client, adset_id, token, {"status": "DELETED"})
            await _post(client, campaign_id, token, {"status": "DELETED"})
            return
        print(f"  Creative: {creative_id}")

        # ---------------------------------------------------------------
        # Step 4: Create Ad
        # ---------------------------------------------------------------
        print("\n=== STEP 4: Ad ===")
        ad = await _post(client, f"{act}/ads", token, {
            "name": "KAN IG Ventas - Atiende 24/7",
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",
        })
        ad_id = ad.get("id")
        if not ad_id:
            await _post(client, adset_id, token, {"status": "DELETED"})
            await _post(client, campaign_id, token, {"status": "DELETED"})
            return
        print(f"  Ad: {ad_id}")

        # ---------------------------------------------------------------
        # Summary
        # ---------------------------------------------------------------
        print(f"""
{'=' * 50}
CAMPANA CREADA (PAUSED)
{'=' * 50}
  Campaign: {campaign_id}
  Ad Set:   {adset_id}
  Creative: {creative_id}
  Ad:       {ad_id}
  Budget:   ${daily_budget_mxn} MXN/dia
  Target:   MX, 25-65, Advantage+
  Opt:      WhatsApp Conversations
  Status:   PAUSED

  Para activar:
  https://www.facebook.com/adsmanager/manage/campaigns?act={account_id}
""")


if __name__ == "__main__":
    asyncio.run(main())
