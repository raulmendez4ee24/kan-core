"""Tests for lead generator: missing API key, dedupe, best-email selection."""
from __future__ import annotations

import asyncio
import os
import pytest

from tools.lead_generator import (
    LEAD_SCHEMA_KEYS,
    clean_domain,
    deduplicate_leads,
    export_sheets,
    generate_lead,
    pick_best_email,
    run_lead_generation,
    validate_email,
)


def test_missing_api_key_raises() -> None:
    """When GOOGLE_API_KEY is missing, run_lead_generation raises ValueError with clear message."""
    # Unset so we don't rely on env
    old = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="Missing GOOGLE_API_KEY"):
            asyncio.run(run_lead_generation(api_key="", max_leads=1))
    finally:
        if old is not None:
            os.environ["GOOGLE_API_KEY"] = old


def test_dedupe_fallback_when_website_empty() -> None:
    """When website is empty, dedupe by (normalized_company, address)."""
    a = generate_lead(
        "1", company="Acme Dental", website="", address="Calle 1", place_id=""
    )
    b = generate_lead(
        "2", company="Acme Dental", website="", address="Calle 1", place_id=""
    )
    b["company"] = "acme dental"
    b["notes"] = "calle 1"
    deduped = deduplicate_leads([a, b])
    assert len(deduped) == 1
    assert deduped[0]["lead_id"] == "1"

    # Different address -> both kept
    c = generate_lead(
        "3", company="Acme Dental", website="", address="Calle 2", place_id=""
    )
    deduped2 = deduplicate_leads([a, c])
    assert len(deduped2) == 2


def test_dedupe_by_place_id() -> None:
    """When place_id is set, dedupe by place_id (same company/domain still one row)."""
    a = generate_lead(
        "1", company="Acme", website="acme.com", place_id="ChIJxyz"
    )
    b = generate_lead(
        "2", company="Acme", website="acme.com", place_id="ChIJxyz"
    )
    deduped = deduplicate_leads([a, b])
    assert len(deduped) == 1


def test_dedupe_by_domain_when_no_place_id() -> None:
    """When no place_id but website exists, dedupe by (domain, company)."""
    a = generate_lead("1", company="Acme", website="https://acme.com/", place_id="")
    b = generate_lead("2", company="Acme", website="https://acme.com/", place_id="")
    deduped = deduplicate_leads([a, b])
    assert len(deduped) == 1


def test_pick_best_email_prefers_role_based() -> None:
    """Among same-status emails, prefer info@, contact@, hola@, ventas@."""
    # No MX check -> both "unknown"; prefer contact@ over random
    email, status = pick_best_email(["random@example.org", "contact@example.org"], smtp_check=False)
    assert email == "contact@example.org"
    assert status == "unknown"


def test_pick_best_email_avoids_noreply() -> None:
    """noreply@ and similar are skipped; other email is chosen."""
    email, status = pick_best_email(["noreply@example.org", "info@example.org"], smtp_check=False)
    assert email == "info@example.org"


def test_pick_best_email_empty_list() -> None:
    """Empty list returns empty email and unknown."""
    email, status = pick_best_email([], smtp_check=False)
    assert email == ""
    assert status == "unknown"


def test_pick_best_email_only_noreply_returns_empty_or_first() -> None:
    """If only noreply, we filter it out so validated is empty -> return ('', 'unknown')."""
    email, status = pick_best_email(["noreply@example.org"], smtp_check=False)
    assert email == ""
    assert status == "unknown"


def test_generate_lead_includes_internal_fields() -> None:
    """Lead dict includes place_id, phone, rating, maps_url for debugging."""
    lead = generate_lead(
        "1",
        company="Test",
        website="https://test.com",
        place_id="ChIJx",
        phone="+52 1",
        rating=4.5,
        maps_url="https://maps.google.com/",
    )
    assert lead["place_id"] == "ChIJx"
    assert lead["phone"] == "+52 1"
    assert lead["rating"] == 4.5
    assert lead["maps_url"] == "https://maps.google.com/"


def test_export_csv_columns_strict() -> None:
    """CSV export uses only LEAD_SCHEMA_KEYS (no place_id, phone, etc.)."""
    import csv
    import tempfile
    from tools.lead_generator import export_csv

    lead = generate_lead("1", company="C", place_id="pid", phone="123")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        path = f.name
    try:
        export_csv([lead], path)
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            assert list(row.keys()) == LEAD_SCHEMA_KEYS
            assert "place_id" not in row
            assert "phone" not in row
    finally:
        os.unlink(path)


def test_clean_domain() -> None:
    """clean_domain strips protocol and trailing slash."""
    assert clean_domain("https://clinicadental.com/") == "clinicadental.com"
    assert clean_domain("http://foo.com") == "foo.com"
    assert clean_domain("") == ""


def test_export_sheets_assigns_new_lead_ids_when_existing_ids_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSheet:
        def __init__(self) -> None:
            self.appended_rows = []

        def get_all_values(self):
            return [
                LEAD_SCHEMA_KEYS,
                ["1"] + [""] * (len(LEAD_SCHEMA_KEYS) - 1),
                ["2"] + [""] * (len(LEAD_SCHEMA_KEYS) - 1),
            ]

        def append_rows(self, rows, value_input_option="USER_ENTERED"):
            self.appended_rows.extend(rows)

        def update_cell(self, row, col, value):
            raise AssertionError("update_cell should not be called in append mode")

    class FakeBook:
        def __init__(self, sheet) -> None:
            self._sheet = sheet

        def worksheet(self, name: str):
            assert name == "leads"
            return self._sheet

    fake_sheet = FakeSheet()
    fake_book = FakeBook(fake_sheet)
    monkeypatch.setattr("tools.google_sheets_client.ensure_worksheet_with_headers", lambda *args, **kwargs: True)
    monkeypatch.setattr("tools.google_sheets_client.open_spreadsheet", lambda spreadsheet_id: fake_book)

    leads = [
        generate_lead("1", company="A"),
        generate_lead("2", company="B"),
        generate_lead("2", company="C"),
    ]

    ok = export_sheets(leads, "sheet-id", upsert=False)

    assert ok is True
    assert [row[0] for row in fake_sheet.appended_rows] == ["3", "4", "5"]
    assert [lead["lead_id"] for lead in leads] == ["3", "4", "5"]
