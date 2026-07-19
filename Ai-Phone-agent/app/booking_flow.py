"""Phone-friendly calendar booking conversation flow."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.calendar_service import (
    create_booking,
    fetch_slots_on_date,
    fetch_upcoming_slots,
    format_day_label,
    format_slot_speech,
    get_booking_context,
    get_calendar_timezone,
    has_booking_at_time,
    is_weekend,
    weekend_closed_message,
)
from app.config import reload_settings

logger = logging.getLogger(__name__)

BOOKING_STEPS = ("slot_choice", "name")

ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
    "sixth": 5,
    "6th": 5,
}

HOUR_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

TIME_PATTERN = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?|am|pm)\b",
    re.IGNORECASE,
)
MERIDIEM_PATTERN = re.compile(r"(a\.?\s*m\.?|p\.?\s*m\.?|am|pm)", re.IGNORECASE)

MONTH_NAMES: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTH_PATTERN = "|".join(sorted(MONTH_NAMES.keys(), key=len, reverse=True))
SPOKEN_DATE_PATTERN = re.compile(
    rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
SPOKEN_DATE_PATTERN_ALT = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+({_MONTH_PATTERN})\b",
    re.IGNORECASE,
)
DAY_ONLY_PATTERN = re.compile(
    r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b",
    re.IGNORECASE,
)


def normalize_spoken_email(text: str) -> str:
    """Turn spoken email like 'john at gmail dot com' into john@gmail.com."""
    cleaned = text.strip().lower()
    cleaned = cleaned.replace(" at ", "@").replace(" dot ", ".")
    cleaned = re.sub(r"\s+", "", cleaned)
    if "@" in cleaned and "." in cleaned.split("@", 1)[-1]:
        return cleaned
    return text.strip()


def _slots_from_state(state: dict) -> list[datetime]:
    tz_name = state.get("timezone") or str(get_calendar_timezone())
    tz = ZoneInfo(tz_name)
    slots: list[datetime] = []
    for iso in state.get("slots", []):
        try:
            slots.append(datetime.fromisoformat(iso).astimezone(tz))
        except ValueError:
            continue
    return slots


def _sample_slots_for_speech(slots: list[datetime], max_show: int = 6) -> list[datetime]:
    """Pick times spread across the day so afternoon slots are mentioned too."""
    if len(slots) <= max_show:
        return slots
    step = (len(slots) - 1) / (max_show - 1)
    indices = {0, len(slots) - 1}
    for i in range(1, max_show - 1):
        indices.add(int(round(i * step)))
    return [slots[i] for i in sorted(indices)]


def _format_slot_options(slots: list[datetime]) -> str:
    if not slots:
        return "I don't see any open times in the next week."

    shown = _sample_slots_for_speech(slots)
    parts: list[str] = []
    current_day = ""
    for slot in shown:
        day = format_day_label(slot)
        time_text = format_slot_speech(slot)
        if day != current_day:
            parts.append(f"{day} at {time_text}")
            current_day = day
        else:
            parts.append(time_text)

    if len(parts) == 1:
        options = parts[0]
    elif len(parts) == 2:
        options = f"{parts[0]} and {parts[1]}"
    else:
        options = ", ".join(parts[:-1]) + f", and {parts[-1]}"

    extra = ""
    if len(slots) > len(shown):
        extra = " You can say any time during business hours, like 3 PM."
    return f"I have openings {options}.{extra}"


def _parse_time_from_speech(speech: str) -> Optional[tuple[int, int, str]]:
    """Parse hour (24h), minute, and meridiem letter (a/p) from speech."""
    lowered = speech.lower().strip()
    match = TIME_PATTERN.search(lowered)
    if not match:
        return None

    hour_raw = match.group(1).lower()
    hour = int(hour_raw) if hour_raw.isdigit() else HOUR_WORDS.get(hour_raw, 0)
    if not hour:
        return None

    minute = int(match.group(2) or 0)
    meridiem = match.group(3).replace(".", "").replace(" ", "").lower()
    if meridiem.startswith("p") and hour != 12:
        hour += 12
    elif meridiem.startswith("a") and hour == 12:
        hour = 0
    return hour, minute, meridiem[0]


def _find_slot_by_time(slots: list[datetime], hour: int, minute: int) -> Optional[datetime]:
    for slot in slots:
        if slot.hour == hour and slot.minute == minute:
            return slot
    return None


def parse_slot_choice(speech: str, slots: list[datetime]) -> Optional[datetime]:
    """Match caller speech to one of the available slots."""
    if not slots:
        return None

    lowered = speech.lower().strip()

    # Prefer explicit times like "3 PM" over ordinals like "three" = third option
    parsed = _parse_time_from_speech(speech)
    if parsed:
        hour, minute, _meridiem = parsed
        return _find_slot_by_time(slots, hour, minute)

    if not MERIDIEM_PATTERN.search(lowered):
        for word, idx in ORDINALS.items():
            if re.search(rf"\b{re.escape(word)}\b", lowered) and idx < len(slots):
                return slots[idx]

    return None


def slot_choice_error(speech: str, slots: list[datetime]) -> str:
    """Helpful retry message when the caller's time wasn't matched."""
    parsed = _parse_time_from_speech(speech)
    if parsed:
        hour, minute, meridiem = parsed
        h12 = hour % 12 or 12
        ampm = "AM" if meridiem == "a" else "PM"
        time_label = f"{h12}:{minute:02d} {ampm}" if minute else f"{h12} {ampm}"
        shown = ", ".join(format_slot_speech(s) for s in _sample_slots_for_speech(slots, 4))
        return (
            f"I'm sorry, {time_label} isn't available. "
            f"I have {shown}. Which of those works, or try another time?"
        )
    return (
        "I didn't catch that time. Please say the time again, "
        "like 3 PM, or say first or second for one of the options."
    )


def parse_spoken_date(speech: str, conversation_hint: str = "") -> Optional[date]:
    """Parse a calendar date like 'August 16th' or 'the 17th' from caller speech."""
    tz = get_calendar_timezone()
    today = datetime.now(tz).date()
    combined = f"{speech} {conversation_hint}".lower()
    lowered = speech.lower()

    for text in (lowered,):
        match = SPOKEN_DATE_PATTERN.search(text)
        if match:
            month = MONTH_NAMES[match.group(1).lower()]
            day = int(match.group(2))
            return _resolve_date(today, month, day)

        match = SPOKEN_DATE_PATTERN_ALT.search(text)
        if match:
            day = int(match.group(1))
            month = MONTH_NAMES[match.group(2).lower()]
            return _resolve_date(today, month, day)

    day_match = DAY_ONLY_PATTERN.search(lowered)
    if day_match:
        day = int(day_match.group(1))
        for text in (combined,):
            for name, month_num in MONTH_NAMES.items():
                if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text):
                    return _resolve_date(today, month_num, day)
        if "next month" in combined:
            from app.calendar_service import _next_month_range

            now = datetime.now(tz)
            _, _, year, month = _next_month_range(now)
            return _resolve_date(today, month, day, year=year)
        if day >= today.day:
            return _resolve_date(today, today.month, day)
        if today.month == 12:
            return _resolve_date(today, 1, day, year=today.year + 1)
        return _resolve_date(today, today.month + 1, day)

    return None


def _resolve_date(today: date, month: int, day: int, year: Optional[int] = None) -> Optional[date]:
    year = year or today.year
    try:
        target = date(year, month, day)
    except ValueError:
        return None
    if target < today:
        try:
            target = date(year + 1, month, day)
        except ValueError:
            return None
    return target


def _booking_state_from_slots(
    slots: list[datetime],
    caller_phone: str,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    tz = ZoneInfo(ctx["timezone"])
    return {
        "step": "slot_choice",
        "slots": [s.isoformat() for s in slots],
        "timezone": str(tz),
        "caller_phone": caller_phone,
        **{k: v for k, v in ctx.items() if k != "timezone"},
    }


def start_booking_for_speech(
    speech: str,
    caller_phone: str = "",
    conversation_hint: str = "",
) -> tuple[dict[str, Any], str]:
    """
    Start booking when the caller names a specific date (e.g. 'August 16th').
    Creates a real booking flow — never a fake AI confirmation.
    """
    ctx = get_booking_context()
    if not ctx:
        return {}, (
            "I can't access the live calendar right now. "
            "Say leave a message and someone will call you back to schedule."
        )

    target = parse_spoken_date(speech, conversation_hint=conversation_hint)
    if not target:
        return {}, ""

    if is_weekend(target):
        return {}, weekend_closed_message(target)

    tz = ZoneInfo(ctx["timezone"])
    slots = fetch_slots_on_date(target, max_slots=24)
    day_label = target.strftime("%A, %B %d, %Y")

    if not slots:
        return {}, (
            f"I'm sorry, I don't see any open times on {day_label}. "
            "Would you like to try a different day?"
        )

    state = _booking_state_from_slots(slots, caller_phone, ctx)
    parsed_time = _parse_time_from_speech(speech)
    if parsed_time:
        hour, minute, _meridiem = parsed_time
        chosen = _find_slot_by_time(slots, hour, minute)
        if chosen:
            state["selected_slot"] = chosen.isoformat()
            state["step"] = "name"
            when = f"{format_day_label(chosen)} at {format_slot_speech(chosen)}"
            return state, f"Great, {when}. May I have your name to complete the booking?"

    times = ", ".join(format_slot_speech(s) for s in _sample_slots_for_speech(slots, 6))
    prompt = (
        f"On {day_label}, I have {times} available. "
        "Which time works for you?"
    )
    return state, prompt


def start_booking_flow(
    day_offset: Optional[int] = None,
    caller_phone: str = "",
) -> tuple[dict[str, Any], str]:
    """
    Fetch live calendar slots and return initial flow state + spoken prompt.
    day_offset: 0=today, 1=tomorrow, None=next 7 days.
    """
    ctx = get_booking_context()
    if not ctx:
        return {}, (
            "I can't access the live calendar right now. "
            "Say leave a message and someone will call you back to schedule."
        )

    tz = ZoneInfo(ctx["timezone"])
    slots = fetch_upcoming_slots(day_offset=day_offset, max_slots=24)
    if not slots:
        return {}, (
            "I'm sorry, there are no open demo times in the next week. "
            "Would you like to leave a message so our team can find a time for you?"
        )

    state = {
        "step": "slot_choice",
        "slots": [s.isoformat() for s in slots],
        "timezone": str(tz),
        "caller_phone": caller_phone,
        **{k: v for k, v in ctx.items() if k != "timezone"},
    }
    prompt = (
        f"{_format_slot_options(slots)} "
        "Which time would you like me to book for you?"
    )
    return state, prompt


def get_booking_prompt(state: dict) -> Optional[str]:
    """Return the next question for the current booking step."""
    step = state.get("step", "slot_choice")
    prompts = {
        "slot_choice": "Which time works best? You can say something like 2 PM.",
        "name": "Great. May I have your name for the booking?",
    }
    return prompts.get(step)


def is_slot_selection(speech: str, state: dict) -> bool:
    """True when speech looks like the caller picking a booking time."""
    if state.get("step") != "slot_choice":
        return True
    slots = _slots_from_state(state)
    return parse_slot_choice(speech, slots) is not None


def process_booking_input(state: dict, speech: str) -> tuple[dict, Optional[str]]:
    """
    Advance booking state from caller speech.
    Returns (updated_state, error_message). error_message prompts a retry.
    """
    step = state.get("step", "slot_choice")
    slots = _slots_from_state(state)

    if step == "slot_choice":
        chosen = parse_slot_choice(speech, slots)
        if not chosen:
            return state, slot_choice_error(speech, slots)
        caller_phone = state.get("caller_phone", "")
        if caller_phone and has_booking_at_time(caller_phone, chosen):
            day = format_day_label(chosen)
            when = format_slot_speech(chosen)
            return state, (
                f"You already have a demo booked for {day} at {when}. "
                "Would you like to pick a different time?"
            )
        state["selected_slot"] = chosen.isoformat()
        state["step"] = "name"
        return state, None

    if step == "name":
        name = speech.strip()
        if len(name) < 2:
            return state, "Sorry, I didn't get your name. Could you say your name again?"
        state["name"] = name
        return state, None

    return state, None


def is_booking_ready_to_confirm(state: dict) -> bool:
    """True when slot and name are collected and not already booked."""
    if state.get("booking_status") == "confirmed":
        return False
    return bool(state.get("selected_slot") and state.get("name"))


def finalize_booking(state: dict) -> tuple[bool, str]:
    """
    Book the selected slot on Google Calendar or Calendly.
    Returns (success, spoken_confirmation).
    Idempotent — won't create duplicate events for the same phone and time.
    """
    if state.get("booking_status") == "confirmed":
        return True, state.get(
            "confirmation_message",
            "That appointment is already confirmed.",
        )

    if not get_booking_context():
        return False, "I couldn't complete the booking. Please try again later."

    tz_name = state.get("timezone") or str(get_calendar_timezone())
    tz = ZoneInfo(tz_name)

    try:
        start_local = datetime.fromisoformat(state["selected_slot"]).astimezone(tz)
    except (KeyError, ValueError):
        return False, "Something went wrong with the time you picked. Let's try again."

    name = state.get("name", "Guest")
    phone = state.get("caller_phone", "")
    when = format_slot_speech(start_local)
    day = format_day_label(start_local)
    full_date = start_local.strftime("%A, %B %d, %Y")

    if phone and has_booking_at_time(phone, start_local):
        msg = (
            f"You already have a demo booked on {full_date} at {when}. "
            "I didn't add a duplicate."
        )
        state["booking_status"] = "confirmed"
        state["confirmation_message"] = msg
        return True, msg

    extra = {}
    if state.get("event_type_uri"):
        extra["event_type_uri"] = state["event_type_uri"]

    ok, detail = create_booking(start_local, name, phone=phone, extra=extra)
    if not ok:
        logger.warning("Calendar booking failed: %s", detail)
        return False, (
            "I'm sorry, I couldn't complete the booking right now. "
            "Would you like to try a different time?"
        )

    msg = (
        f"You're all set, {name}. I've booked your NM2TECH demo for {day} at {when}. "
        "Our team will call you to confirm. "
        "Is there anything else I can help with?"
    )
    state["booking_status"] = "confirmed"
    state["confirmation_message"] = msg
    state["booked_event_id"] = detail
    return True, msg
