#!/usr/bin/env python3
"""
Run HunterOperator with real Google Places data and print a formatted report.

Usage:
    GOOGLE_API_KEY=... python scripts/run_hunter.py

Exports top-10 opportunities to hunter_results.csv.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from pathlib import Path

# Allow imports from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.revenue_operators.hunter import HunterOperator

BUSINESS_TYPES = ["dentists", "clinics", "spas", "barbershops", "restaurants"]
PRODUCTS = ["paginas web personalizadas", "chatbots"]
CITY = "Guadalajara"
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "hunter_results.csv"


async def main() -> None:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    hunter = HunterOperator()

    print("Scanning Google Maps...", flush=True)
    scanned = await hunter.scan_google_maps(CITY, BUSINESS_TYPES, 5000)

    print(f"Evaluating {len(scanned)} opportunities...", flush=True)
    scored = [await hunter.evaluate_opportunity(item) for item in scanned]
    scored.sort(key=lambda x: x.score, reverse=True)
    top10 = scored[:10]

    # Generate outreach for each top-10 opportunity
    print("Generating offers and outreach messages...", flush=True)
    outreach_map: dict[str, str] = {}
    for opp in top10:
        offer = await hunter.generate_offer(opp, PRODUCTS)
        msg = await hunter.generate_outreach(opp, offer, "whatsapp")
        outreach_map[opp.business_name] = msg.body

    # -----------------------------------------------------------------------
    # Console report
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print(f"  HUNTER REPORT — {CITY}")
    print("=" * 70)
    print(f"  Total scanned: {len(scanned)}")
    print(f"  Top 10 opportunities:")
    print()

    for rank, opp in enumerate(top10, start=1):
        phone = str(opp.metadata.get("phone") or opp.metadata.get("formatted_phone_number") or "—")
        website = str(opp.metadata.get("website") or "—")
        outreach_body = outreach_map.get(opp.business_name, "—")

        print(f"  #{rank:02d}  {opp.business_name}")
        print(f"       Vertical : {opp.vertical}")
        print(f"       City     : {opp.city}")
        print(f"       Score    : {opp.score:.1f}")
        print(f"       Phone    : {phone}")
        print(f"       Website  : {website}")
        print(f"       Outreach : {outreach_body[:100]}{'...' if len(outreach_body) > 100 else ''}")
        print()

    print("=" * 70)
    print()

    # -----------------------------------------------------------------------
    # CSV export
    # -----------------------------------------------------------------------
    rows = []
    for opp in top10:
        phone = str(opp.metadata.get("phone") or opp.metadata.get("formatted_phone_number") or "")
        website = str(opp.metadata.get("website") or "")
        rows.append({
            "business_name": opp.business_name,
            "vertical": opp.vertical,
            "city": opp.city,
            "score": round(opp.score, 2),
            "phone": phone,
            "website": website,
            "outreach_message": outreach_map.get(opp.business_name, ""),
        })

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["business_name", "vertical", "city", "score", "phone", "website", "outreach_message"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Results exported to: {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
