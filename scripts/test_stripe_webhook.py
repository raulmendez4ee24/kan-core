#!/usr/bin/env python3
"""
Simulate a real Stripe checkout.session.completed webhook hitting production.

Usage:
    python scripts/test_stripe_webhook.py
    STRIPE_WEBHOOK_SECRET=whsec_... python scripts/test_stripe_webhook.py

Signs the payload exactly as Stripe does, so the production endpoint's
signature verification will accept it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENDPOINT = "https://web-production-dbb6b.up.railway.app/payments/webhook"

WH_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
if not WH_SECRET:
    print("ERROR: STRIPE_WEBHOOK_SECRET not set in environment")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Build a realistic checkout.session.completed payload
# ---------------------------------------------------------------------------

FAKE_LEAD_ID = "test-lead-webhook-001"
FAKE_SESSION_ID = "cs_test_simulated_123456789"

payload: dict = {
    "id": "evt_test_simulated_001",
    "object": "event",
    "api_version": "2023-10-16",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": FAKE_SESSION_ID,
            "object": "checkout.session",
            "payment_status": "paid",
            "status": "complete",
            "currency": "mxn",
            "amount_total": 500000,          # 5,000.00 MXN in centavos
            "amount_subtotal": 500000,
            "customer_email": "cliente@ejemplo.com",
            "metadata": {
                "lead_id": FAKE_LEAD_ID,
                "product": "CRM Pack Básico",
                "amount_mxn": "5000.0",
            },
            "payment_intent": "pi_test_simulated_001",
            "mode": "payment",
        }
    },
    "livemode": False,
    "created": int(time.time()),
}

# ---------------------------------------------------------------------------
# Sign the payload exactly as Stripe does
# ---------------------------------------------------------------------------

raw_body: bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
timestamp: int = int(time.time())
signed_payload: bytes = f"{timestamp}.".encode() + raw_body
v1_sig: str = hmac.new(WH_SECRET.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
stripe_signature: str = f"t={timestamp},v1={v1_sig}"

# ---------------------------------------------------------------------------
# Fire the request
# ---------------------------------------------------------------------------

print(f"→ Endpoint : {ENDPOINT}")
print(f"→ Lead ID  : {FAKE_LEAD_ID}")
print(f"→ Amount   : $5,000.00 MXN")
print(f"→ Signature: t={timestamp},v1={v1_sig[:16]}...")
print()

try:
    resp = httpx.post(
        ENDPOINT,
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": stripe_signature,
        },
        timeout=20.0,
    )
except httpx.RequestError as exc:
    print(f"ERROR: request failed — {exc}")
    sys.exit(1)

print(f"← Status : {resp.status_code}")
try:
    body = resp.json()
    print(f"← Body   : {json.dumps(body, indent=2, ensure_ascii=False)}")
except Exception:
    print(f"← Body   : {resp.text[:500]}")

if resp.status_code == 200:
    print("\n✓ Webhook accepted and processed correctly.")
else:
    print(f"\n✗ Unexpected status {resp.status_code}.")
    sys.exit(1)
