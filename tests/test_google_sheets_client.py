"""Tests for Google Sheets helper batching behavior."""
from __future__ import annotations

from tools.google_sheets_client import batch_update_by_header


def test_batch_update_by_header_uses_batch_request(monkeypatch) -> None:
    class FakeSheet:
        def __init__(self) -> None:
            self.batch_calls = []

        def get_all_values(self):
            return [
                ["lead_id", "email_validation_status", "personalization_snippet", "status", "notes"],
                ["1", "", "", "", ""],
                ["2", "", "", "", ""],
            ]

        def batch_update(self, requests, value_input_option="USER_ENTERED"):
            self.batch_calls.append((requests, value_input_option))

    class FakeBook:
        def __init__(self, sheet) -> None:
            self._sheet = sheet

        def worksheet(self, name: str):
            assert name == "leads"
            return self._sheet

    fake_sheet = FakeSheet()
    monkeypatch.setattr(
        "tools.google_sheets_client.open_spreadsheet",
        lambda spreadsheet_id: FakeBook(fake_sheet),
    )

    ok = batch_update_by_header(
        spreadsheet_id="sheet-id",
        tab_name="leads",
        key_column="lead_id",
        updates_by_key={
            "1": {
                "email_validation_status": "valid",
                "personalization_snippet": "Snippet",
                "status": "queued",
                "notes": "meta",
            }
        },
    )

    assert ok is True
    assert len(fake_sheet.batch_calls) == 1
    requests, value_input_option = fake_sheet.batch_calls[0]
    assert value_input_option == "USER_ENTERED"
    assert requests == [
        {
            "range": "B2:E2",
            "values": [["valid", "Snippet", "queued", "meta"]],
        }
    ]


def test_batch_update_by_header_returns_false_when_key_column_missing(monkeypatch) -> None:
    class FakeSheet:
        def get_all_values(self):
            return [["company", "status"], ["Acme", "new"]]

    class FakeBook:
        def worksheet(self, name: str):
            return FakeSheet()

    monkeypatch.setattr(
        "tools.google_sheets_client.open_spreadsheet",
        lambda spreadsheet_id: FakeBook(),
    )

    ok = batch_update_by_header(
        spreadsheet_id="sheet-id",
        tab_name="leads",
        key_column="lead_id",
        updates_by_key={"1": {"status": "queued"}},
    )

    assert ok is False
