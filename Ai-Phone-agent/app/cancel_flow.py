"""Phone-friendly appointment cancellation flow."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.booking_flow import parse_spoken_date
from app.calendar_service import (
    cancel_appointment,
    find_upcoming_bookings,
    format_day_label,
    format_slot_speech,
    get_calendar_timezone,
)

logger = logging.getLogger(__name__)

CANCEL_KEYWORDS = {
    "cancel",
    "cancellation",
    "call off",
    "don't need",
    "do not need",
    "remove my appointment",
    "delete my appointment",
}

CONFIRM_YES = {"yes", "yeah", "yep", "sure", "confirm", "do it", "go ahead", "please", "correct"}
CONFIRM_NO = {"no", "nope", "keep it", "never mind", "nevermind", "don't", "do not"}


def detect_cancel_intent(text: str) -> bool:
    """True when caller wants to cancel an appointment."""
    lowered = text.lower()
    if any(kw in lowered for kw in CANCEL_KEYWORDS):
        return True
    return "cancel" in lowered and ("appointment" in lowered or "demo" in lowered or "booking" in lowered)


def cancel_intent_from_context(speech: str, conversation_hint: str) -> bool:
    """True when this turn continues a cancellation (e.g. caller names a date next)."""
    return detect_cancel_intent(speech) or (
        bool(parse_spoken_date(speech, conversation_hint)) and "cancel" in conversation_hint.lower()
    )


def _detect_yes(text: str) -> bool:
    lowered = text.lower().strip()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in CONFIRM_YES)


def _detect_no(text: str) -> bool:
    lowered = text.lower().strip()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in CONFIRM_NO)


def is_cancel_confirmation(speech: str) -> bool:
    """True when the caller is answering yes/no, not naming a different date."""
    if parse_spoken_date(speech):
        return False
    return _detect_yes(speech) or _detect_no(speech)


def _booking_to_state(booking: dict[str, Any], caller_phone: str) -> dict[str, Any]:
    start = booking["start"]
    return {
        "step": "confirm",
        "event_id": booking["event_id"],
        "calendar_id": booking.get("calendar_id", ""),
        "start": start.isoformat(),
        "timezone": str(get_calendar_timezone()),
        "caller_phone": caller_phone,
        "name": booking.get("name", ""),
    }


def _confirm_prompt(booking: dict[str, Any]) -> str:
    start = booking["start"]
    day = format_day_label(start)
    when = format_slot_speech(start)
    full_date = start.strftime("%A, %B %d, %Y")
    return (
        f"I found your demo on {full_date} at {when}. "
        "Should I cancel it? Say yes to confirm, or no to keep it."
    )


def _pick_booking(
    bookings: list[dict[str, Any]],
    speech: str = "",
    conversation_hint: str = "",
) -> tuple[Optional[dict[str, Any]], str]:
    """Match the booking the caller wants to cancel, optionally by date in speech."""
    if not bookings:
        return None, (
            "I couldn't find an upcoming appointment for your phone number. "
            "Would you like to schedule a new demo instead?"
        )

    target = parse_spoken_date(speech, conversation_hint) if speech.strip() else None
    if target:
        matched = [b for b in bookings if b["start"].date() == target]
        if matched:
            return matched[0], _confirm_prompt(matched[0])
        day_label = target.strftime("%A, %B %d, %Y")
        parts = [
            f"{b['start'].strftime('%A, %B %d')} at {format_slot_speech(b['start'])}"
            for b in bookings[:3]
        ]
        return None, (
            f"I don't see an appointment on {day_label}. "
            f"You have demos on {' and '.join(parts)}. "
            "Which date would you like to cancel?"
        )

    if len(bookings) == 1:
        return bookings[0], _confirm_prompt(bookings[0])

    parts = [
        f"{b['start'].strftime('%A, %B %d')} at {format_slot_speech(b['start'])}"
        for b in bookings[:3]
    ]
    return None, (
        f"You have appointments on {' and '.join(parts)}. "
        "Which date would you like to cancel?"
    )


def start_cancel_flow(
    caller_phone: str,
    speech: str = "",
    conversation_hint: str = "",
) -> tuple[dict[str, Any], str]:
    """
    Look up the caller's demo matching the requested date (if given) and ask to confirm.
    Returns (state, spoken_prompt). Empty state if nothing found or date not matched.
    """
    bookings = find_upcoming_bookings(caller_phone)
    booking, prompt = _pick_booking(bookings, speech, conversation_hint)
    if not booking:
        return {}, prompt
    return _booking_to_state(booking, caller_phone), prompt


def try_retarget_cancel(
    caller_phone: str,
    speech: str,
    conversation_hint: str = "",
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    If the caller names a different date during cancel, return new (state, prompt).
    Returns (None, None) when speech is a yes/no confirmation.
    """
    if is_cancel_confirmation(speech):
        return None, None
    if not parse_spoken_date(speech, conversation_hint):
        return None, None
    state, prompt = start_cancel_flow(caller_phone, speech, conversation_hint)
    return (state if state else None), prompt


def process_cancel_input(state: dict, speech: str) -> tuple[dict, Optional[str], Optional[str]]:
    """
    Handle yes/no during cancellation confirmation.
    Returns (state, error_message, success_message).
    One of error or success will be set when done.
    """
    step = state.get("step", "confirm")
    if step != "confirm":
        return state, "Should I cancel your appointment? Say yes or no.", None

    if _detect_no(speech):
        return {}, None, "No problem. Your appointment is still scheduled. Anything else I can help with?"

    if not _detect_yes(speech):
        return state, "Please say yes to cancel, or no to keep your appointment.", None

    ok, detail = cancel_appointment(
        event_id=state.get("event_id", ""),
        calendar_id=state.get("calendar_id", ""),
        caller_phone=state.get("caller_phone", ""),
    )
    if not ok:
        logger.warning("Cancel failed: %s", detail)
        return state, (
            "I'm sorry, I couldn't cancel that appointment right now. "
            "Please try again or say transfer to speak with someone."
        ), None

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(state.get("timezone", "America/New_York"))
        start = datetime.fromisoformat(state["start"]).astimezone(tz)
        full_date = start.strftime("%A, %B %d, %Y")
        when = format_slot_speech(start)
    except (KeyError, ValueError):
        full_date, when = "your scheduled day", "the booked time"

    return {}, None, (
        f"Done. Your demo on {full_date} at {when} has been cancelled. "
        "Is there anything else I can help with?"
    )
