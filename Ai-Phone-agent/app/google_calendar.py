"""Google Calendar API — availability checks and event booking."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import reload_settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def google_calendar_is_ready() -> bool:
    """True when Google Calendar credentials and calendar ID are configured."""
    cfg = reload_settings()
    if not (cfg.google_calendar_id or "").strip():
        return False
    if (cfg.google_service_account_file or "").strip():
        return os.path.isfile(cfg.google_service_account_file)
    return bool(
        (cfg.google_refresh_token or "").strip()
        and (cfg.google_client_id or "").strip()
        and (cfg.google_client_secret or "").strip()
    )


def _get_timezone() -> ZoneInfo:
    cfg = reload_settings()
    tz_name = cfg.calendar_timezone or cfg.calendly_timezone or "America/New_York"
    return ZoneInfo(tz_name)


def _get_service_account_email() -> str:
    """Return service account client_email from JSON key file, if configured."""
    cfg = reload_settings()
    sa_file = (cfg.google_service_account_file or "").strip()
    if not sa_file or not os.path.isfile(sa_file):
        return ""
    import json

    with open(sa_file, encoding="utf-8") as f:
        return json.load(f).get("client_email", "")


def _resolve_write_calendar_id(service) -> str:
    """
    Calendar ID used to create events.
    Falls back to the service account's own calendar when the configured
    calendar is not shared with write access.
    """
    cfg = reload_settings()
    configured = (cfg.google_calendar_id or "").strip()
    fallback = _get_service_account_email()

    for calendar_id in (configured, fallback):
        if not calendar_id:
            continue
        try:
            service.events().list(calendarId=calendar_id, maxResults=1).execute()
            if calendar_id != configured and configured:
                logger.warning(
                    "Cannot write to %s — using service account calendar %s. "
                    "Share your calendar with %s (Make changes to events).",
                    configured,
                    calendar_id,
                    fallback,
                )
            return calendar_id
        except Exception as exc:
            logger.debug("Calendar %s not writable: %s", calendar_id, exc)

    return configured or fallback


def _resolve_read_calendar_id(service) -> str:
    """Calendar ID used for free/busy checks."""
    return _resolve_write_calendar_id(service)


def _get_calendar_service():
    """Build an authenticated Google Calendar API client."""
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    cfg = reload_settings()
    creds = None

    sa_file = (cfg.google_service_account_file or "").strip()
    if sa_file and os.path.isfile(sa_file):
        creds = service_account.Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    elif (cfg.google_refresh_token or "").strip():
        creds = Credentials(
            token=None,
            refresh_token=cfg.google_refresh_token.strip(),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg.google_client_id.strip(),
            client_secret=cfg.google_client_secret.strip(),
            scopes=SCOPES,
        )
        creds.refresh(Request())

    if creds is None:
        raise RuntimeError("Google Calendar credentials are not configured")

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _busy_periods(
    service,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Return list of (start, end) busy intervals."""
    body = {
        "timeMin": time_min.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "timeMax": time_max.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "timeZone": str(tz),
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    periods: list[tuple[datetime, datetime]] = []
    for block in busy:
        start = datetime.fromisoformat(block["start"].replace("Z", "+00:00")).astimezone(tz)
        end = datetime.fromisoformat(block["end"].replace("Z", "+00:00")).astimezone(tz)
        periods.append((start, end))
    return periods


def _overlaps(slot_start: datetime, slot_end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    for b_start, b_end in busy:
        if slot_start < b_end and slot_end > b_start:
            return True
    return False


def _candidate_slots(
    tz: ZoneInfo,
    day_offset: Optional[int],
    max_slots: int,
) -> list[datetime]:
    """Generate open appointment slots from business hours minus busy times."""
    cfg = reload_settings()
    duration = timedelta(minutes=cfg.appointment_duration_minutes)
    start_hour = cfg.business_hours_start
    end_hour = cfg.business_hours_end
    now = datetime.now(tz)

    if day_offset is not None:
        first_day = (now + timedelta(days=day_offset)).date()
        last_day = first_day + timedelta(days=1)
    else:
        first_day = now.date()
        last_day = first_day + timedelta(days=7)

    range_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=tz)
    range_end = datetime(last_day.year, last_day.month, last_day.day, tzinfo=tz)

    service = _get_calendar_service()
    calendar_id = _resolve_read_calendar_id(service)
    busy = _busy_periods(service, calendar_id, range_start, range_end, tz)

    slots: list[datetime] = []
    day = first_day
    while day < last_day and len(slots) < max_slots:
        if day.weekday() < 5:  # Mon–Fri
            for hour in range(start_hour, end_hour):
                for minute in (0, 30):
                    if cfg.appointment_duration_minutes == 60 and minute != 0:
                        continue
                    slot_start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
                    slot_end = slot_start + duration
                    if slot_end.hour > end_hour or (slot_end.hour == end_hour and slot_end.minute > 0):
                        continue
                    if slot_start <= now + timedelta(hours=1):
                        continue
                    if not _overlaps(slot_start, slot_end, busy):
                        slots.append(slot_start)
                        if len(slots) >= max_slots:
                            break
                if len(slots) >= max_slots:
                    break
        day += timedelta(days=1)

    return sorted(slots)


def fetch_upcoming_slots(
    day_offset: Optional[int] = None,
    tz: Optional[ZoneInfo] = None,
    max_slots: int = 8,
    *,
    start_date=None,
    end_date=None,
) -> list[datetime]:
    """Return available Google Calendar slots."""
    tz = tz or _get_timezone()
    try:
        if start_date is not None and end_date is not None:
            return _candidate_slots_between(
                tz, start_date=start_date, end_date=end_date, max_slots=max_slots
            )
        return _candidate_slots(tz, day_offset=day_offset, max_slots=max_slots)
    except Exception as exc:
        logger.exception("Google Calendar availability check failed: %s", exc)
        return []


def _candidate_slots_between(
    tz: ZoneInfo,
    start_date,
    end_date,
    max_slots: int,
) -> list[datetime]:
    """Generate open slots between two dates (inclusive start, exclusive end)."""
    cfg = reload_settings()
    duration = timedelta(minutes=cfg.appointment_duration_minutes)
    start_hour = cfg.business_hours_start
    end_hour = cfg.business_hours_end
    now = datetime.now(tz)

    range_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    range_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz)

    service = _get_calendar_service()
    calendar_id = _resolve_read_calendar_id(service)
    busy = _busy_periods(service, calendar_id, range_start, range_end, tz)

    slots: list[datetime] = []
    day = start_date
    while day < end_date and len(slots) < max_slots:
        if day.weekday() < 5:
            for hour in range(start_hour, end_hour):
                for minute in (0, 30):
                    if cfg.appointment_duration_minutes == 60 and minute != 0:
                        continue
                    slot_start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
                    slot_end = slot_start + duration
                    if slot_end.hour > end_hour or (slot_end.hour == end_hour and slot_end.minute > 0):
                        continue
                    if slot_start <= now + timedelta(hours=1):
                        continue
                    if not _overlaps(slot_start, slot_end, busy):
                        slots.append(slot_start)
                        if len(slots) >= max_slots:
                            break
                if len(slots) >= max_slots:
                    break
        day += timedelta(days=1)

    return sorted(slots)


def create_booking(
    start_time_local: datetime,
    name: str,
    phone: str = "",
    email: str = "",
    tz: Optional[ZoneInfo] = None,
) -> tuple[bool, str]:
    """Create a calendar event. Returns (success, detail)."""
    tz = tz or _get_timezone()
    cfg = reload_settings()
    duration = timedelta(minutes=cfg.appointment_duration_minutes)
    end_time = start_time_local + duration

    description_parts = [f"Demo booked via NM2TECH AI Phone Agent.", f"Name: {name}"]
    if phone:
        description_parts.append(f"Phone: {phone}")
    if email:
        description_parts.append(f"Email: {email}")

    event = {
        "summary": f"NM2TECH Demo — {name}",
        "description": "\n".join(description_parts),
        "start": {"dateTime": start_time_local.isoformat(), "timeZone": str(tz)},
        "end": {"dateTime": end_time.isoformat(), "timeZone": str(tz)},
        "reminders": {"useDefault": True},
    }

    # Service accounts cannot invite external attendees without domain-wide delegation.
    if email and "@" in email and not _get_service_account_email():
        event["attendees"] = [{"email": email, "displayName": name}]

    try:
        service = _get_calendar_service()
        calendar_id = _resolve_write_calendar_id(service)
        created = (
            service.events()
            .insert(
                calendarId=calendar_id,
                body=event,
                sendUpdates="none",
            )
            .execute()
        )
        event_id = created.get("id", "created")
        logger.info("Google Calendar event created: %s for %s", event_id, name)
        return True, event_id
    except Exception as exc:
        logger.exception("Google Calendar booking failed: %s", exc)
        return False, str(exc)


def _normalize_phone_digits(phone: str) -> str:
    import re

    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _phone_matches_event(phone: str, event: dict) -> bool:
    """True if the caller phone appears in the event summary or description."""
    digits = _normalize_phone_digits(phone)
    if not digits:
        return False
    haystack = f"{event.get('summary', '')} {event.get('description', '')}"
    haystack_digits = "".join(c for c in haystack if c.isdigit())
    return digits in haystack_digits or phone in haystack


def _same_start_time(a: datetime, b: datetime) -> bool:
    return a.astimezone(b.tzinfo).replace(second=0, microsecond=0) == b.replace(
        second=0, microsecond=0
    )


def has_booking_at_time(
    phone: str,
    start_time: datetime,
    tz: Optional[ZoneInfo] = None,
) -> bool:
    """True if this caller already has a demo booked at the exact start time."""
    if not phone:
        return False
    tz = tz or _get_timezone()
    start_local = start_time.astimezone(tz)
    for booking in find_bookings_by_phone(phone, tz=tz, days_ahead=90):
        if _same_start_time(booking["start"], start_local):
            return True
    return False


def find_bookings_by_phone(
    phone: str,
    tz: Optional[ZoneInfo] = None,
    days_ahead: int = 30,
) -> list[dict]:
    """Return upcoming NM2TECH demo events matching the caller's phone number."""
    tz = tz or _get_timezone()
    now = datetime.now(tz)
    time_max = now + timedelta(days=days_ahead)

    try:
        service = _get_calendar_service()
        calendar_id = _resolve_write_calendar_id(service)
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=now.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                timeMax=time_max.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            )
            .execute()
        )

        bookings: list[dict] = []
        for event in result.get("items", []):
            if event.get("status") == "cancelled":
                continue
            summary = event.get("summary", "")
            if "NM2TECH Demo" not in summary and "NM2TECH demo" not in summary:
                continue
            if not _phone_matches_event(phone, event):
                continue

            start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
            if not start_raw:
                continue
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz)
            if start_dt < now:
                continue

            bookings.append(
                {
                    "event_id": event.get("id", ""),
                    "calendar_id": calendar_id,
                    "summary": summary,
                    "start": start_dt,
                    "name": summary.replace("NM2TECH Demo — ", "").replace("NM2TECH Demo - ", ""),
                }
            )
        return bookings
    except Exception as exc:
        logger.exception("Failed to find bookings for %s: %s", phone, exc)
        return []


def cancel_booking(event_id: str, calendar_id: str = "") -> tuple[bool, str]:
    """Delete a calendar event. Returns (success, detail)."""
    if not event_id:
        return False, "Missing event id"
    try:
        service = _get_calendar_service()
        cal_id = calendar_id or _resolve_write_calendar_id(service)
        service.events().delete(calendarId=cal_id, eventId=event_id).execute()
        logger.info("Google Calendar event cancelled: %s", event_id)
        return True, event_id
    except Exception as exc:
        logger.exception("Google Calendar cancel failed: %s", exc)
        return False, str(exc)
