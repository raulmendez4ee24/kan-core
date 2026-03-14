from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from brain.booking_connector import (
    BookingConnector,
    BookingRequest,
    get_default_booking_connector,
)

UTC = timezone.utc


class FakeGoogleClient:
    def __init__(self, *, events: list[dict[str, Any]] | None = None) -> None:
        self.calendar_id = "primary"
        self._events = list(events or [])
        self.created_events: list[dict[str, Any]] = []

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        calendar_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return list(self._events)

    async def create_event(
        self,
        *,
        summary: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
        contact_name: str | None,
        contact_email: str | None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "id": f"evt-{len(self.created_events) + 1}",
            "hangoutLink": "https://meet.google.com/fake-meet",
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_at.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end_at.isoformat(), "timeZone": timezone_name},
            "calendar_id": calendar_id or self.calendar_id,
            "contact_name": contact_name,
            "contact_email": contact_email,
        }
        self.created_events.append(payload)
        return payload


class FakeSession:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []


@pytest.fixture
def isolated_dbs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKING_CONNECTOR_DB_PATH", str(tmp_path / "booking_connector.sqlite3"))
    monkeypatch.setenv("ATTRIBUTION_ENGINE_DB_PATH", str(tmp_path / "attribution_engine.sqlite3"))


def test_check_available_slots_excludes_busy_ranges(isolated_dbs: None) -> None:
    start = datetime(2026, 3, 14, 16, 0, tzinfo=UTC)
    end = datetime(2026, 3, 14, 18, 0, tzinfo=UTC)
    google = FakeGoogleClient(
        events=[
            {
                "status": "confirmed",
                "start": {"dateTime": "2026-03-14T16:30:00+00:00"},
                "end": {"dateTime": "2026-03-14T17:00:00+00:00"},
            }
        ]
    )
    connector = BookingConnector(google_client=google)

    slots = asyncio.run(connector.check_available_slots(start_at=start, end_at=end, slot_minutes=30))

    assert [slot.start_at for slot in slots] == [
        "2026-03-14T16:00:00+00:00",
        "2026-03-14T17:00:00+00:00",
        "2026-03-14T17:30:00+00:00",
    ]


def test_create_booking_persists_tracks_and_confirms(isolated_dbs: None) -> None:
    tracked: list[dict[str, Any]] = []
    sent_messages: list[dict[str, Any]] = []
    ingested: list[dict[str, Any]] = []

    async def fake_track_booking(**kwargs: Any) -> None:
        tracked.append(kwargs)

    async def fake_sender(*, from_number: str, to: str, text: str) -> dict[str, Any]:
        sent_messages.append({"from_number": from_number, "to": to, "text": text})
        return {"ok": True}

    async def fake_ingest(
        _session: Any,
        *,
        organization_id: Any,
        event_type: str,
        source: str,
        channel: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        ingested.append(
            {
                "organization_id": str(organization_id),
                "event_type": event_type,
                "source": source,
                "channel": channel,
                "entity_id": entity_id,
                "payload": payload,
            }
        )

    connector = BookingConnector(
        google_client=FakeGoogleClient(),
        whatsapp_sender=fake_sender,
        attribution_booking_tracker=fake_track_booking,
        business_event_ingester=fake_ingest,
    )
    org_id = uuid4()
    start = datetime(2026, 3, 15, 17, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)

    record = asyncio.run(
        connector.create_booking(
            BookingRequest(
                lead_id="lead-001",
                organization_id=str(org_id),
                summary="Demo KAN Logic",
                start_at=start,
                end_at=end,
                contact_name="Raul",
                contact_phone="+527731718888",
                contact_email="raul@example.com",
                campaign_id="cmp-demo-001",
                description="Demo comercial",
            ),
            session=FakeSession(),
        )
    )

    assert record.booking_id == "evt-1"
    assert record.event_id == "evt-1"
    assert record.meet_url == "https://meet.google.com/fake-meet"
    assert record.status == "scheduled"
    assert tracked and tracked[0]["campaign_id"] == "cmp-demo-001"
    assert sent_messages and "Yo ya confirmé tu reunión" in sent_messages[0]["text"]
    assert ingested and ingested[0]["event_type"] == "appointment_created"

    fetched = asyncio.run(connector.get_booking("evt-1"))
    assert fetched.summary == "Demo KAN Logic"
    assert fetched.contact_phone == "+527731718888"


def test_process_due_reminders_sends_whatsapp_and_marks_sent(isolated_dbs: None) -> None:
    sent_messages: list[str] = []

    async def fake_sender(*, from_number: str, to: str, text: str) -> dict[str, Any]:
        sent_messages.append(text)
        return {"ok": True}

    connector = BookingConnector(
        google_client=FakeGoogleClient(),
        whatsapp_sender=fake_sender,
    )
    start = datetime(2026, 3, 16, 18, 0, tzinfo=UTC)
    end = start + timedelta(minutes=45)
    booking = asyncio.run(
        connector.create_booking(
            BookingRequest(
                lead_id="lead-reminder",
                summary="Reunión de diagnóstico",
                start_at=start,
                end_at=end,
                contact_phone="+525511111111",
            )
        )
    )

    sent = asyncio.run(connector.process_due_reminders(now=start - timedelta(hours=23)))
    updated = asyncio.run(connector.get_booking(booking.booking_id))

    assert sent == 1
    assert any("Yo te recuerdo" in message for message in sent_messages)
    assert updated.reminder_sent_at is not None


def test_mark_overdue_no_shows_marks_booking_without_breaking(isolated_dbs: None) -> None:
    connector = BookingConnector(google_client=FakeGoogleClient())
    start = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    booking = asyncio.run(
        connector.create_booking(
            BookingRequest(
                lead_id="lead-no-show",
                summary="Implementación",
                start_at=start,
                end_at=end,
            )
        )
    )

    updated = asyncio.run(
        connector.mark_overdue_no_shows(now=end + timedelta(minutes=45), grace_minutes=30)
    )
    record = asyncio.run(connector.get_booking(booking.booking_id))

    assert updated == 1
    assert record.status == "no_show"
    assert record.attended_at is not None
