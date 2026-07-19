"""Unified calendar provider — Google Calendar (default) or Calendly."""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import reload_settings

logger = logging.getLogger(__name__)

AVAILABILITY_KEYWORDS = {
    "available",
    "availability",
    "opening",
    "openings",
    "free",
    "slot",
    "slots",
    "book tomorrow",
    "any time",
    "what times",
    "when can",
    "when are you",
}

DAY_KEYWORDS = {
    "today": 0,
    "tomorrow": 1,
}


def _provider() -> str:
    cfg = reload_settings()
    explicit = (cfg.calendar_provider or "").strip().lower()
    if explicit in ("google", "calendly"):
        return explicit
    from app.google_calendar import google_calendar_is_ready

    if google_calendar_is_ready():
        return "google"
    from app.calendly_client import calendly_is_ready

    if calendly_is_ready():
        return "calendly"
    return ""


def calendar_is_ready() -> bool:
    """True when any calendar provider is configured."""
    return bool(_provider())


def get_calendar_timezone() -> ZoneInfo:
    cfg = reload_settings()
    tz_name = cfg.calendar_timezone or cfg.calendly_timezone or "America/New_York"
    return ZoneInfo(tz_name)


def today_context_line() -> str:
    """Current date/time for the AI — prevents wrong years like 2023."""
    tz = get_calendar_timezone()
    now = datetime.now(tz)
    date_spoken = now.strftime("%A, %B %d, %Y")
    if now.month == 12:
        next_month_label = f"January {now.year + 1}"
    else:
        next_month_label = datetime(now.year, now.month + 1, 1, tzinfo=tz).strftime("%B %Y")
    return (
        f"TODAY: {date_spoken} ({tz}). "
        f"The current year is {now.year}. "
        f"'Next month' means {next_month_label}. "
        f"When the caller gives a month and day without a year, use {now.year} or later - never a past year. "
        f"Business hours: Monday through Friday only. Closed Saturdays and Sundays."
    )


def is_weekend(day: date) -> bool:
    """True on Saturday or Sunday."""
    return day.weekday() >= 5


def weekend_closed_message(target: date) -> str:
    """Spoken reply when the caller picks a weekend date."""
    day_label = target.strftime("%A, %B %d")
    return (
        f"We're closed on Saturdays and Sundays, so {day_label} isn't available. "
        "Would you like to pick a weekday instead?"
    )


def detect_availability_intent(text: str) -> bool:
    """True when caller asks about open times on a specific day (for booking flow day pick)."""
    lowered = text.lower()
    if any(p in lowered for p in ("do i have", "my appointment", "are there appointment", "any appointment")):
        return False
    has_avail = any(kw in lowered for kw in AVAILABILITY_KEYWORDS)
    has_day = any(kw in lowered for kw in DAY_KEYWORDS) or "appointment" in lowered
    return has_avail and has_day


def _period_label(speech: str) -> str:
    lowered = speech.lower()
    if "next month" in lowered:
        return "next month"
    if "this month" in lowered:
        return "this month"
    if "next week" in lowered:
        return "next week"
    if "tomorrow" in lowered:
        return "tomorrow"
    if "today" in lowered:
        return "today"
    return "in the next week"


def _next_month_range(now: datetime) -> tuple[date, date, int, int]:
    if now.month == 12:
        year, month = now.year + 1, 1
    else:
        year, month = now.year, now.month + 1
    last_day = monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day) + timedelta(days=1)
    return start, end, year, month


def fetch_slots_for_speech(speech: str, max_slots: int = 16) -> list[datetime]:
    """Fetch open calendar slots matching the time period mentioned in speech."""
    tz = get_calendar_timezone()
    now = datetime.now(tz)
    lowered = speech.lower()

    if "next month" in lowered:
        start, end, _, _ = _next_month_range(now)
        return fetch_upcoming_slots(start_date=start, end_date=end, tz=tz, max_slots=max_slots)

    if "next week" in lowered:
        start = (now + timedelta(days=7)).date()
        end = start + timedelta(days=7)
        return fetch_upcoming_slots(start_date=start, end_date=end, tz=tz, max_slots=max_slots)

    if "this month" in lowered:
        last_day = monthrange(now.year, now.month)[1]
        start = date(now.year, now.month, 1)
        end = date(now.year, now.month, last_day) + timedelta(days=1)
        return fetch_upcoming_slots(start_date=start, end_date=end, tz=tz, max_slots=max_slots)

    day_offset = availability_day_offset(speech)
    if any(kw in lowered for kw in DAY_KEYWORDS):
        return fetch_upcoming_slots(day_offset=day_offset, tz=tz, max_slots=max_slots)

    return fetch_upcoming_slots(day_offset=None, tz=tz, max_slots=max_slots)


def build_calendar_context(speech: str, phone: str) -> str:
    """Live calendar facts for OpenAI — caller bookings and open demo slots."""
    tz = get_calendar_timezone()
    now = datetime.now(tz)
    lowered = speech.lower()
    lines: list[str] = [today_context_line()]

    bookings = find_upcoming_bookings(phone)
    if "next month" in lowered:
        _, _, year, month = _next_month_range(now)
        bookings = [b for b in bookings if b["start"].month == month and b["start"].year == year]
    elif "this month" in lowered:
        bookings = [
            b for b in bookings if b["start"].month == now.month and b["start"].year == now.year
        ]

    if bookings:
        parts = [
            f"{b['start'].strftime('%A, %B %d, %Y')} at {format_slot_speech(b['start'])}"
            for b in bookings
        ]
        lines.append(f"Caller's booked appointments: {'; '.join(parts)}.")
    else:
        lines.append(
            f"Caller has no booked appointments {_period_label(speech)} matching their phone number."
        )

    slots = fetch_slots_for_speech(speech)
    if slots:
        parts = [
            f"{s.strftime('%A, %B %d, %Y')} at {format_slot_speech(s)}" for s in slots[:8]
        ]
        suffix = f" ({len(slots)} open total)" if len(slots) > 8 else ""
        lines.append(f"Open demo times {_period_label(speech)}: {'; '.join(parts)}{suffix}.")
    else:
        if "saturday" in lowered or "sunday" in lowered or "weekend" in lowered:
            lines.append(
                "No open demo times on that day — the office is closed Saturdays and Sundays."
            )
        else:
            lines.append(f"No open demo times {_period_label(speech)}.")

    lines.append("To book, the caller must pick a time and give their name — you cannot confirm bookings yourself.")
    return " ".join(lines)


def fetch_slots_on_date(target: date, max_slots: int = 24) -> list[datetime]:
    """Return open slots on one calendar day."""
    tz = get_calendar_timezone()
    end = target + timedelta(days=1)
    return fetch_upcoming_slots(
        start_date=target,
        end_date=end,
        tz=tz,
        max_slots=max_slots,
    )


def availability_day_offset(speech: str) -> int:
    lowered = speech.lower()
    if "today" in lowered:
        return 0
    if "tomorrow" in lowered:
        return 1
    return 1


def format_slot_speech(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    if dt.minute:
        return f"{hour}:{dt.minute:02d} {ampm}"
    return f"{hour} {ampm}"


def format_day_label(dt: datetime) -> str:
    from datetime import timedelta

    tz = dt.tzinfo or get_calendar_timezone()
    now = datetime.now(tz)
    if dt.date() == now.date():
        return "today"
    if dt.date() == (now + timedelta(days=1)).date():
        return "tomorrow"
    try:
        return dt.strftime("%A, %B %-d")
    except ValueError:
        return f"{dt.strftime('%A, %B')} {dt.day}"


def fetch_upcoming_slots(
    day_offset: Optional[int] = None,
    max_slots: int = 8,
    *,
    start_date=None,
    end_date=None,
    tz: Optional[ZoneInfo] = None,
) -> list[datetime]:
    tz = tz or get_calendar_timezone()
    provider = _provider()
    if provider == "google":
        from app.google_calendar import fetch_upcoming_slots as google_slots

        return google_slots(
            day_offset=day_offset,
            tz=tz,
            max_slots=max_slots,
            start_date=start_date,
            end_date=end_date,
        )
    if provider == "calendly":
        from app.calendly_client import (
            fetch_upcoming_slots as calendly_slots,
            get_calendly_context,
            resolve_event_type_uri,
        )

        ctx = get_calendly_context()
        if not ctx:
            return []
        token, link, event_uri, _tz = ctx
        if not event_uri:
            event_uri = resolve_event_type_uri(token, link)
        if not event_uri:
            return []
        return calendly_slots(token, event_uri, day_offset=day_offset, tz=tz, max_slots=max_slots)
    return []


def create_booking(
    start_time_local: datetime,
    name: str,
    phone: str = "",
    email: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    tz = get_calendar_timezone()
    provider = _provider()
    extra = extra or {}

    if provider == "google":
        from app.google_calendar import create_booking as google_book

        return google_book(start_time_local, name, phone=phone, email=email, tz=tz)

    if provider == "calendly":
        from app.calendly_client import create_booking as calendly_book, get_calendly_context

        ctx = get_calendly_context()
        if not ctx:
            return False, "Calendly not configured"
        token, _link, event_uri, cal_tz = ctx
        event_uri = extra.get("event_type_uri") or event_uri
        if not event_uri:
            return False, "Missing event type"
        if not email:
            return False, "Email required for Calendly booking"
        return calendly_book(token, event_uri, start_time_local, name, email, tz=cal_tz)

    return False, "No calendar provider configured"


def get_booking_context() -> Optional[dict[str, Any]]:
    """Provider-specific context stored in booking flow state."""
    provider = _provider()
    if provider == "google":
        return {"provider": "google", "timezone": str(get_calendar_timezone())}
    if provider == "calendly":
        from app.calendly_client import get_calendly_context, resolve_event_type_uri

        ctx = get_calendly_context()
        if not ctx:
            return None
        token, link, event_uri, tz = ctx
        if not event_uri:
            event_uri = resolve_event_type_uri(token, link)
        if not event_uri:
            return None
        return {
            "provider": "calendly",
            "timezone": str(tz),
            "event_type_uri": event_uri,
        }
    return None


def find_upcoming_bookings(phone: str, days_ahead: int = 90) -> list[dict[str, Any]]:
    """Find upcoming booked demos for a caller phone number."""
    if _provider() == "google":
        from app.google_calendar import find_bookings_by_phone

        return find_bookings_by_phone(phone, tz=get_calendar_timezone(), days_ahead=days_ahead)
    return []


def has_booking_at_time(phone: str, start_time: datetime) -> bool:
    """True if caller already has a demo at this exact start time."""
    if _provider() == "google":
        from app.google_calendar import has_booking_at_time as google_has_booking

        return google_has_booking(phone, start_time, tz=get_calendar_timezone())
    return False


def cancel_appointment(
    event_id: str,
    calendar_id: str = "",
    caller_phone: str = "",
) -> tuple[bool, str]:
    """Cancel a booked appointment. Returns (success, detail)."""
    if _provider() == "google":
        from app.google_calendar import cancel_booking

        return cancel_booking(event_id, calendar_id=calendar_id)
    return False, "Cancellation not supported for this calendar provider"
