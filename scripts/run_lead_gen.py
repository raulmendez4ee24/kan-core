#!/usr/bin/env python3
"""
Run lead generation: Google Places + scrape + email validation.
Output: CSV and/or Google Sheet (Cold Email Ops schema).

Usage:
  pip install -r requirements.txt
  export GOOGLE_API_KEY=your_places_api_key
  export KEYWORD="dentist"
  export LOCATION="Guadalajara Mexico"
  export MAX_LEADS=100
  export SMTP_CHECK=false
  export COLD_EMAIL_SPREADSHEET_ID=optional_sheet_id

  python scripts/run_lead_gen.py [--csv leads.csv] [--sheets SPREADSHEET_ID] [--max 100] [--keyword "dentist"] [--location "Guadalajara Mexico"]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Lead generation: Places + scrape + export")
    parser.add_argument("--csv", default="", help="Export CSV path (e.g. leads.csv)")
    parser.add_argument("--sheets", default="", help="Google Sheet ID to append leads tab")
    parser.add_argument("--max", type=int, default=0, help="Max leads (default from MAX_LEADS env)")
    parser.add_argument("--keyword", default="", help="Search keyword (default from KEYWORD env)")
    parser.add_argument("--location", default="", help="Location (default from LOCATION env)")
    parser.add_argument("--smtp", action="store_true", help="Enable SMTP/MX check for emails")
    parser.add_argument("--upsert", action="store_true", help="Sheets: update by lead_id if exists")
    args = parser.parse_args()

    from tools.lead_generator import run_lead_generation

    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    csv_path = args.csv or os.getenv("LEAD_GEN_CSV", "")
    sheets_id = args.sheets or os.getenv("COLD_EMAIL_SPREADSHEET_ID", "")
    max_leads = args.max or int(os.getenv("MAX_LEADS", "100"))
    keyword = args.keyword or os.getenv("KEYWORD", "dentist")
    location = args.location or os.getenv("LOCATION", "Guadalajara Mexico")

    if not api_key:
        print("Missing GOOGLE_API_KEY", file=sys.stderr)
        return 1

    try:
        leads = await run_lead_generation(
            keyword=keyword,
            location=location,
            api_key=api_key,
            max_leads=max_leads,
            smtp_check=args.smtp,
            export_csv_path=csv_path or None,
            export_sheets_id=sheets_id or None,
            export_sheets_upsert=args.upsert,
        )
    except ValueError as e:
        if "GOOGLE_API_KEY" in str(e):
            print("Missing GOOGLE_API_KEY", file=sys.stderr)
            return 1
        raise
    print(f"Generated {len(leads)} leads.")
    return 0 if leads else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
