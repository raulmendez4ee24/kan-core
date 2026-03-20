from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote
from uuid import UUID

import aiosqlite
import httpx
from pydantic import BaseModel, ConfigDict, Field

from brain.attribution_engine import track_booking
from brain.business_monitor import ingest_business_event
from brain.creative_event_tracker import record_sales_outcome, record_whatsapp_outcome
from observability import capture_exception
from tools.ycloud_client import send_ycloud_whatsapp_text_message

UTC = timezone.utc
DEFAULT_TIMEZONE = "America/Mexico_City"
DEFAULT_REMINDER_HOURS = 24
_GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    token = str(value or "").strip()
    if not token:
        raise ValueError("datetime value is required")
    parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _normalize_optional(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _resolve_source_key(
    *,
    campaign_id: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    source_campaign: Optional[str] = None,
    ad_name: Optional[str] = None,
) -> Optional[str]:
    return (
        _normalize_optional(campaign_id)
        or _normalize_optional(utm_campaign)
        or _normalize_optional(source_campaign)
        or _normalize_optional(ad_name)
    )


def _db_path() -> Path:
    configured = str(os.getenv("BOOKING_CONNECTOR_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "booking_connector.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(str(_db_path())) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await _ensure_schema(conn)
        yield conn


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL,
            organization_id TEXT,
            channel TEXT NOT NULL,
            contact_name TEXT,
            contact_phone TEXT,
            contact_email TEXT,
            source_key TEXT,
            campaign_id TEXT,
            utm_campaign TEXT,
            source_campaign TEXT,
            ad_name TEXT,
            calendar_id TEXT NOT NULL,
            event_id TEXT,
            summary TEXT NOT NULL,
            description TEXT,
            timezone TEXT NOT NULL,
            scheduled_start TIMESTAMP NOT NULL,
            scheduled_end TIMESTAMP NOT NULL,
            meet_url TEXT,
            status TEXT NOT NULL,
            reminder_due_at TIMESTAMP,
            reminder_sent_at TIMESTAMP,
            attended_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_bookings_schedule
            ON bookings(scheduled_start, status);
        CREATE INDEX IF NOT EXISTS idx_bookings_source_key
            ON bookings(source_key);
        CREATE INDEX IF NOT EXISTS idx_bookings_phone
            ON bookings(contact_phone);
        """
    )
    await conn.commit()


async def _fetchone(
    conn: aiosqlite.Connection,
    query: str,
    params: tuple[Any, ...] = (),
) -> Optional[aiosqlite.Row]:
    cursor = await conn.execute(query, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


class AvailableSlot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_at: str
    end_at: str
    timezone: str = DEFAULT_TIMEZONE


class BookingRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: str
    organization_id: Optional[str] = None
    summary: str
    start_at: datetime | str
    end_at: datetime | str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    description: Optional[str] = None
    channel: str = "whatsapp"
    timezone: str = DEFAULT_TIMEZONE
    campaign_id: Optional[str] = None
    utm_campaign: Optional[str] = None
    source_campaign: Optional[str] = None
    ad_name: Optional[str] = None
    calendar_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BookingRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    booking_id: str
    lead_id: str
    organization_id: Optional[str] = None
    channel: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    source_key: Optional[str] = None
    campaign_id: Optional[str] = None
    event_id: Optional[str] = None
    summary: str
    description: Optional[str] = None
    timezone: str
    scheduled_start: str
    scheduled_end: str
    meet_url: Optional[str] = None
    status: str
    reminder_due_at: Optional[str] = None
    reminder_sent_at: Optional[str] = None
    attended_at: Optional[str] = None
    created_at: str
    updated_at: str


class _GoogleClient:
    def __init__(
        self,
        *,
        access_token: Optional[str] = None,
        api_key: Optional[str] = None,
        calendar_id: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.access_token = _normalize_optional(access_token or os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN"))
        self.api_key = _normalize_optional(api_key or os.getenv("GOOGLE_CALENDAR_API_KEY"))
        self.calendar_id = _normalize_optional(calendar_id or os.getenv("GOOGLE_CALENDAR_ID")) or "primary"
        self._http_client = http_client

    @asynccontextmanager
    async def _client(self):
        if self._http_client is not None:
            yield self._http_client
            return
        async with httpx.AsyncClient(timeout=15.0) as client:
            yield client

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _auth_mode(self) -> str:
        if self.access_token:
            return "bearer"
        if self.api_key:
            return "api_key"
        return "none"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = dict(params or {})
        if not self.access_token and self.api_key:
            query["key"] = self.api_key
        url = f"{_GOOGLE_CALENDAR_BASE_URL}{path}"
        async with self._client() as client:
            response = await client.request(
                method.upper(),
                url,
                headers=self._headers(),
                params=query,
                json=json_payload,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    async def list_events(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        calendar_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cid = quote(calendar_id or self.calendar_id, safe="")
        payload = await self._request(
            "GET",
            f"/calendars/{cid}/events",
            params={
                "timeMin": start_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "timeMax": end_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 250,
            },
        )
        return list(payload.get("items") or [])

    async def create_event(
        self,
        *,
        summary: str,
        description: Optional[str],
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
        contact_name: Optional[str],
        contact_email: Optional[str],
        calendar_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("Google Calendar write access requires GOOGLE_CALENDAR_ACCESS_TOKEN")
        cid = quote(calendar_id or self.calendar_id, safe="")
        request_id = hashlib.sha256(
            f"{summary}|{start_at.isoformat()}|{end_at.isoformat()}|{contact_email or ''}".encode("utf-8")
        ).hexdigest()[:24]
        attendees = []
        if _normalize_optional(contact_email):
            attendee: Dict[str, Any] = {"email": str(contact_email).strip()}
            if _normalize_optional(contact_name):
                attendee["displayName"] = str(contact_name).strip()
            attendees.append(attendee)
        payload = {
            "summary": summary,
            "description": description or "",
            "start": {
                "dateTime": start_at.isoformat(),
                "timeZone": timezone_name,
            },
            "end": {
                "dateTime": end_at.isoformat(),
                "timeZone": timezone_name,
            },
            "attendees": attendees,
            "conferenceData": {
                "createRequest": {
                    "requestId": request_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        return await self._request(
            "POST",
            f"/calendars/{cid}/events",
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json_payload=payload,
        )


class BookingConnector:
    def __init__(
        self,
        *,
        google_client: Optional[_GoogleClient] = None,
        reminder_hours: int = DEFAULT_REMINDER_HOURS,
        whatsapp_sender: Callable[..., Awaitable[Optional[Dict[str, Any]]]] = send_ycloud_whatsapp_text_message,
        attribution_booking_tracker: Callable[..., Awaitable[Any]] = track_booking,
        business_event_ingester: Callable[..., Awaitable[Any]] = ingest_business_event,
    ) -> None:
        self.google = google_client or _GoogleClient()
        self.reminder_hours = max(1, int(reminder_hours or DEFAULT_REMINDER_HOURS))
        self._whatsapp_sender = whatsapp_sender
        self._track_booking = attribution_booking_tracker
        self._ingest_business_event = business_event_ingester

    async def check_available_slots(
        self,
        *,
        start_at: datetime | str,
        end_at: datetime | str,
        slot_minutes: int = 30,
        calendar_id: Optional[str] = None,
    ) -> List[AvailableSlot]:
        start_dt = _parse_dt(start_at)
        end_dt = _parse_dt(end_at)
        if end_dt <= start_dt:
            raise ValueError("end_at must be after start_at")
        safe_slot = max(15, int(slot_minutes or 30))
        events = await self.google.list_events(start_at=start_dt, end_at=end_dt, calendar_id=calendar_id)
        busy_ranges: List[tuple[datetime, datetime]] = []
        for event in events:
            if str(event.get("status") or "").lower() == "cancelled":
                continue
            if str(event.get("transparency") or "").lower() == "transparent":
                continue
            start_raw = ((event.get("start") or {}).get("dateTime") or "").strip()
            end_raw = ((event.get("end") or {}).get("dateTime") or "").strip()
            if not start_raw or not end_raw:
                continue
            busy_ranges.append((_parse_dt(start_raw), _parse_dt(end_raw)))
        busy_ranges.sort(key=lambda item: item[0])

        slots: List[AvailableSlot] = []
        cursor = start_dt
        slot_delta = timedelta(minutes=safe_slot)
        while cursor + slot_delta <= end_dt:
            candidate_end = cursor + slot_delta
            overlaps = any(cursor < busy_end and candidate_end > busy_start for busy_start, busy_end in busy_ranges)
            if not overlaps:
                slots.append(
                    AvailableSlot(
                        start_at=cursor.isoformat(),
                        end_at=candidate_end.isoformat(),
                        timezone=DEFAULT_TIMEZONE,
                    )
                )
            cursor = candidate_end
        return slots

    async def create_booking(
        self,
        request: BookingRequest,
        *,
        session: Any | None = None,
    ) -> BookingRecord:
        start_dt = _parse_dt(request.start_at)
        end_dt = _parse_dt(request.end_at)
        if end_dt <= start_dt:
            raise ValueError("end_at must be after start_at")
        event = await self.google.create_event(
            summary=request.summary,
            description=request.description,
            start_at=start_dt,
            end_at=end_dt,
            timezone_name=request.timezone or DEFAULT_TIMEZONE,
            contact_name=request.contact_name,
            contact_email=request.contact_email,
            calendar_id=request.calendar_id,
        )
        event_id = _normalize_optional(event.get("id"))
        meet_url = _normalize_optional(event.get("hangoutLink"))
        if not meet_url:
            conference = event.get("conferenceData") or {}
            for entry in list(conference.get("entryPoints") or []):
                if str(entry.get("entryPointType") or "").lower() == "video":
                    meet_url = _normalize_optional(entry.get("uri"))
                    if meet_url:
                        break
        now = _now()
        reminder_due_at = start_dt - timedelta(hours=self.reminder_hours)
        source_key = _resolve_source_key(
            campaign_id=request.campaign_id,
            utm_campaign=request.utm_campaign,
            source_campaign=request.source_campaign,
            ad_name=request.ad_name,
        )
        booking_id = event_id or hashlib.sha256(
            f"{request.lead_id}|{request.summary}|{start_dt.isoformat()}".encode("utf-8")
        ).hexdigest()[:24]
        async with _connect() as conn:
            await conn.execute(
                """
                INSERT INTO bookings (
                    booking_id, lead_id, organization_id, channel, contact_name, contact_phone,
                    contact_email, source_key, campaign_id, utm_campaign, source_campaign, ad_name,
                    calendar_id, event_id, summary, description, timezone, scheduled_start,
                    scheduled_end, meet_url, status, reminder_due_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(booking_id) DO UPDATE SET
                    event_id = COALESCE(excluded.event_id, bookings.event_id),
                    meet_url = COALESCE(excluded.meet_url, bookings.meet_url),
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    booking_id,
                    request.lead_id,
                    _normalize_optional(request.organization_id),
                    request.channel,
                    _normalize_optional(request.contact_name),
                    _normalize_optional(request.contact_phone),
                    _normalize_optional(request.contact_email),
                    source_key,
                    _normalize_optional(request.campaign_id),
                    _normalize_optional(request.utm_campaign),
                    _normalize_optional(request.source_campaign),
                    _normalize_optional(request.ad_name),
                    _normalize_optional(request.calendar_id) or self.google.calendar_id,
                    event_id,
                    request.summary,
                    _normalize_optional(request.description),
                    request.timezone or DEFAULT_TIMEZONE,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    meet_url,
                    "scheduled",
                    reminder_due_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await conn.commit()

        await self._track_booking(
            lead_id=request.lead_id,
            scheduled_at=start_dt,
            booking_id=booking_id,
            campaign_id=request.campaign_id,
            utm_campaign=request.utm_campaign,
            source_campaign=request.source_campaign,
            ad_name=request.ad_name,
            confirmed=True,
            metadata={
                "calendar_event_id": event_id,
                "meet_url": meet_url,
            },
        )
        if source_key:
            try:
                record_whatsapp_outcome(source_key, booked=True)
            except Exception as exc:
                capture_exception(exc, {"source": "booking_connector", "phase": "creative_event_tracker.booked"})

        if session is not None and _normalize_optional(request.organization_id):
            try:
                organization_id = UUID(str(request.organization_id))
                await self._ingest_business_event(
                    session,
                    organization_id=organization_id,
                    event_type="appointment_created",
                    source="booking_connector",
                    channel=request.channel,
                    entity_id=booking_id,
                    payload={
                        "lead_id": request.lead_id,
                        "summary": request.summary,
                        "scheduled_start": start_dt.isoformat(),
                        "scheduled_end": end_dt.isoformat(),
                        "meet_url": meet_url,
                        "campaign_id": request.campaign_id,
                    },
                )
            except Exception as exc:
                capture_exception(exc, {"source": "booking_connector", "phase": "appointment_created"})

        if _normalize_optional(request.contact_phone):
            message = self._compose_confirmation_message(
                summary=request.summary,
                start_at=start_dt,
                timezone_name=request.timezone or DEFAULT_TIMEZONE,
                meet_url=meet_url,
            )
            await self._whatsapp_sender(
                from_number="",
                to=str(request.contact_phone),
                text=message,
            )

        return await self.get_booking(booking_id)

    async def get_booking(self, booking_id: str) -> BookingRecord:
        async with _connect() as conn:
            row = await _fetchone(
                conn,
                "SELECT * FROM bookings WHERE booking_id = ?",
                (str(booking_id).strip(),),
            )
        if row is None:
            raise KeyError(f"booking {booking_id} not found")
        return self._row_to_record(row)

    async def process_due_reminders(
        self,
        *,
        now: datetime | str | None = None,
        session: Any | None = None,
    ) -> int:
        now_dt = _parse_dt(now or _now())
        sent = 0
        async with _connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM bookings
                WHERE status = 'scheduled'
                  AND reminder_sent_at IS NULL
                  AND reminder_due_at IS NOT NULL
                  AND reminder_due_at <= ?
                ORDER BY scheduled_start ASC
                """,
                (now_dt.isoformat(),),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            for row in rows:
                phone = _normalize_optional(row["contact_phone"])
                if not phone:
                    continue
                message = self._compose_reminder_message(
                    summary=str(row["summary"]),
                    start_at=_parse_dt(str(row["scheduled_start"])),
                    timezone_name=str(row["timezone"] or DEFAULT_TIMEZONE),
                    meet_url=_normalize_optional(row["meet_url"]),
                )
                await self._whatsapp_sender(from_number="", to=phone, text=message)
                await conn.execute(
                    """
                    UPDATE bookings
                    SET reminder_sent_at = ?, updated_at = ?
                    WHERE booking_id = ?
                    """,
                    (now_dt.isoformat(), now_dt.isoformat(), str(row["booking_id"])),
                )
                sent += 1
                if session is not None and _normalize_optional(row["organization_id"]):
                    try:
                        await self._ingest_business_event(
                            session,
                            organization_id=UUID(str(row["organization_id"])),
                            event_type="appointment_reminder",
                            source="booking_connector",
                            channel=str(row["channel"]),
                            entity_id=str(row["booking_id"]),
                            payload={
                                "lead_id": str(row["lead_id"]),
                                "scheduled_start": str(row["scheduled_start"]),
                            },
                        )
                    except Exception as exc:
                        capture_exception(exc, {"source": "booking_connector", "phase": "appointment_reminder"})
            await conn.commit()
        return sent

    async def mark_booking_attendance(
        self,
        booking_id: str,
        *,
        attended: bool,
        session: Any | None = None,
    ) -> BookingRecord:
        now_dt = _now()
        status = "attended" if attended else "no_show"
        async with _connect() as conn:
            await conn.execute(
                """
                UPDATE bookings
                SET status = ?, attended_at = ?, updated_at = ?
                WHERE booking_id = ?
                """,
                (status, now_dt.isoformat(), now_dt.isoformat(), str(booking_id).strip()),
            )
            cursor = await conn.execute(
                "SELECT * FROM bookings WHERE booking_id = ?",
                (str(booking_id).strip(),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            await conn.commit()
        if row is None:
            raise KeyError(f"booking {booking_id} not found")

        if session is not None and _normalize_optional(row["organization_id"]):
            event_type = "appointment_attended" if attended else "appointment_no_show"
            try:
                await self._ingest_business_event(
                    session,
                    organization_id=UUID(str(row["organization_id"])),
                    event_type=event_type,
                    source="booking_connector",
                    channel=str(row["channel"]),
                    entity_id=str(row["booking_id"]),
                    payload={
                        "lead_id": str(row["lead_id"]),
                        "scheduled_start": str(row["scheduled_start"]),
                    },
                )
            except Exception as exc:
                capture_exception(exc, {"source": "booking_connector", "phase": event_type})
        source_key = _normalize_optional(row["source_key"])
        if attended and source_key:
            try:
                record_sales_outcome(source_key, showed=True)
            except Exception as exc:
                capture_exception(exc, {"source": "booking_connector", "phase": "creative_event_tracker.showed"})

        return self._row_to_record(row)

    async def mark_overdue_no_shows(
        self,
        *,
        now: datetime | str | None = None,
        grace_minutes: int = 30,
        session: Any | None = None,
    ) -> int:
        now_dt = _parse_dt(now or _now())
        threshold = now_dt - timedelta(minutes=max(0, int(grace_minutes or 0)))
        updated = 0
        async with _connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM bookings
                WHERE status = 'scheduled'
                  AND scheduled_end <= ?
                """,
                (threshold.isoformat(),),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        for row in rows:
            await self.mark_booking_attendance(str(row["booking_id"]), attended=False, session=session)
            updated += 1
        return updated

    @staticmethod
    def _compose_confirmation_message(
        *,
        summary: str,
        start_at: datetime,
        timezone_name: str,
        meet_url: Optional[str],
    ) -> str:
        local = start_at.astimezone(UTC)
        when = local.strftime("%Y-%m-%d %H:%M UTC")
        suffix = f" Enlace Meet: {meet_url}" if meet_url else ""
        return f"Yo ya confirmé tu reunión: {summary}. Quedó para {when}.{suffix}"

    @staticmethod
    def _compose_reminder_message(
        *,
        summary: str,
        start_at: datetime,
        timezone_name: str,
        meet_url: Optional[str],
    ) -> str:
        local = start_at.astimezone(UTC)
        when = local.strftime("%Y-%m-%d %H:%M UTC")
        suffix = f" Aquí está tu enlace: {meet_url}" if meet_url else ""
        return f"Yo te recuerdo que mañana tienes: {summary} a las {when}.{suffix}"

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> BookingRecord:
        return BookingRecord(
            booking_id=str(row["booking_id"]),
            lead_id=str(row["lead_id"]),
            organization_id=_normalize_optional(row["organization_id"]),
            channel=str(row["channel"]),
            contact_name=_normalize_optional(row["contact_name"]),
            contact_phone=_normalize_optional(row["contact_phone"]),
            contact_email=_normalize_optional(row["contact_email"]),
            source_key=_normalize_optional(row["source_key"]),
            campaign_id=_normalize_optional(row["campaign_id"]),
            event_id=_normalize_optional(row["event_id"]),
            summary=str(row["summary"]),
            description=_normalize_optional(row["description"]),
            timezone=str(row["timezone"]),
            scheduled_start=str(row["scheduled_start"]),
            scheduled_end=str(row["scheduled_end"]),
            meet_url=_normalize_optional(row["meet_url"]),
            status=str(row["status"]),
            reminder_due_at=_normalize_optional(row["reminder_due_at"]),
            reminder_sent_at=_normalize_optional(row["reminder_sent_at"]),
            attended_at=_normalize_optional(row["attended_at"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


def get_default_booking_connector() -> BookingConnector:
    return BookingConnector()
