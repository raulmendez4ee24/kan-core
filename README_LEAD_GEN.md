# Lead Generator (Google Places + scraping + export)

Generates leads for cold email using **Google Places API**, website scraping, and email validation. Output is compatible with the Cold Email Ops schema (same columns as the `leads` tab).

## Requirements

```bash
pip install -r requirements.txt
```

Uses: `httpx`, `beautifulsoup4`, `tenacity`, `dnspython` (optional MX check).  
**Google Places API** must be enabled in Google Cloud Console (same project as your API key).

## Environment variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Google API key (Places API) | `AIzaSy...` |
| `KEYWORD` | Search keyword | `dentist` |
| `LOCATION` | Location string | `Guadalajara Mexico` |
| `MAX_LEADS` | Max leads per run | `100` |
| `SMTP_CHECK` | Enable MX check for emails | `true` / `false` |
| `COLD_EMAIL_SPREADSHEET_ID` | Optional Sheet ID to append leads | |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Service account JSON (for Sheets export) | |

## Run

```bash
# Using env vars only (exports to CSV if LEAD_GEN_CSV set)
python scripts/run_lead_gen.py

# Export to CSV
python scripts/run_lead_gen.py --csv leads.csv --max 50

# Export to Google Sheet (needs service account)
python scripts/run_lead_gen.py --sheets YOUR_SPREADSHEET_ID --csv leads_backup.csv

# Custom keyword/location
python scripts/run_lead_gen.py --keyword "dental clinic" --location "CDMX Mexico" --max 100
```

## Output schema

Same as Cold Email Ops `leads` tab:

- `lead_id`, `company`, `website`, `first_name`, `last_name`, `title`, `email`, `linkedin`, `country`, `segment`, `status`, `last_contacted_at`, `source`, `notes`, `personalization_snippet`, `email_validation_status`, `score`

Defaults: `country=MX`, `segment=local_business`, `status=new`, `source=google_places`, `score` 70 + bonuses (website +10, email +10, rating ≥4.5 +5).

## Flow

1. **Search** – Google Places Text Search by keyword + location (with pagination).
2. **Details** – For each place, fetch website, phone, address, rating, Maps URL.
3. **Scrape** – If website exists: homepage + `/contact`, `/about`, `/team`, `/contacto`, `/nosotros`.
4. **Emails** – Regex extraction, then format + optional MX validation.
5. **Lead** – Build row with defaults and score; if no email, lead is still created with empty email.
6. **Dedupe** – By (domain, business name).
7. **Export** – CSV and/or append to Google Sheet `leads` tab.

## API key

Use a **Google API key** with **Places API** (and optionally **Places API (New)** if you use the new endpoint) enabled in [Google Cloud Console](https://console.cloud.google.com/apis/library). The key in the spec is for development; for production use a restricted key and env var only.
