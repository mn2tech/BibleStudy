"""Calendly API — availability checks and programmatic booking."""



import logging

from datetime import datetime, timedelta

from typing import Optional

from zoneinfo import ZoneInfo



import httpx



from app.config import reload_settings



logger = logging.getLogger(__name__)



CALENDLY_API = "https://api.calendly.com"

DEFAULT_TZ = ZoneInfo("America/New_York")



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





def detect_availability_intent(text: str) -> bool:

    """True when caller asks if appointments are open on a given day."""

    lowered = text.lower()

    has_avail = any(kw in lowered for kw in AVAILABILITY_KEYWORDS)

    has_day = any(kw in lowered for kw in DAY_KEYWORDS) or "appointment" in lowered

    return has_avail and has_day





def _parse_target_day(speech: str) -> int:

    """Return day offset from today: 0=today, 1=tomorrow (default)."""

    lowered = speech.lower()

    if "today" in lowered:

        return 0

    if "tomorrow" in lowered:

        return 1

    return 1





def _auth_headers(token: str) -> dict[str, str]:

    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}





def calendly_is_ready() -> bool:

    """True when API token and scheduling link are configured."""

    cfg = reload_settings()

    return bool((cfg.calendly_api_token or "").strip() and (cfg.calendly_link or "").strip())





def get_calendly_context() -> Optional[tuple[str, str, str, ZoneInfo]]:

    """Return (token, link, event_type_uri, timezone) or None if not configured."""

    cfg = reload_settings()

    token = (cfg.calendly_api_token or "").strip()

    link = (cfg.calendly_link or "").strip()

    if not token or not link:

        return None

    tz = ZoneInfo(cfg.calendly_timezone or "America/New_York")

    event_uri = (cfg.calendly_event_type_uri or "").strip()

    return token, link, event_uri, tz





def calendly_spoken_hint(link: str = "") -> str:

    """Phone-friendly Calendly URL from config link."""

    cfg = reload_settings()

    url = (link or cfg.calendly_link or "").strip()

    if not url:

        return "our online scheduling page"

    slug = _slug_from_calendly_link(url)

    spoken = slug.replace("-", " ").replace("_", " ")

    return f"calendly dot com slash nm 2 tech 77 slash {spoken}"





def _slug_from_calendly_link(link: str) -> str:

    """Extract event slug from https://calendly.com/nm2tech77/nm2tech-ai-demo."""

    path = link.rstrip("/").split("calendly.com/")[-1]

    parts = path.split("/")

    return parts[-1] if len(parts) >= 2 else "nm2tech-ai-demo"





def resolve_event_type_uri(token: str, calendly_link: str) -> Optional[str]:

    """Look up event type URI from Calendly API using the scheduling link slug."""

    slug = _slug_from_calendly_link(calendly_link)

    try:

        with httpx.Client(timeout=15.0) as client:

            me = client.get(f"{CALENDLY_API}/users/me", headers=_auth_headers(token))

            me.raise_for_status()

            user_uri = me.json()["resource"]["uri"]



            resp = client.get(

                f"{CALENDLY_API}/event_types",

                headers=_auth_headers(token),

                params={"user": user_uri, "active": "true"},

            )

            resp.raise_for_status()

            for item in resp.json().get("collection", []):

                if item.get("slug") == slug:

                    uri = item.get("uri")

                    logger.info("Resolved Calendly event type: %s", uri)

                    return uri

            collection = resp.json().get("collection", [])

            if collection:

                return collection[0].get("uri")

    except Exception as exc:

        logger.exception("Failed to resolve Calendly event type: %s", exc)

    return None





def get_event_type_details(token: str, event_type_uri: str) -> dict:

    """Fetch event type metadata (location, duration, etc.)."""

    try:

        with httpx.Client(timeout=15.0) as client:

            resp = client.get(event_type_uri, headers=_auth_headers(token))

            resp.raise_for_status()

            return resp.json().get("resource", {})

    except Exception as exc:

        logger.exception("Failed to fetch event type details: %s", exc)

        return {}





def fetch_available_slots(

    token: str,

    event_type_uri: str,

    day_offset: int = 1,

    tz: ZoneInfo = DEFAULT_TZ,

) -> list[datetime]:

    """

    Return available slot start times for a given day.

    Uses Calendly GET /event_type_available_times (max 7-day window).

    """

    now = datetime.now(tz)

    target = (now + timedelta(days=day_offset)).date()

    start_local = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=tz)

    end_local = start_local + timedelta(days=1)



    start_utc = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    end_utc = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")



    try:

        with httpx.Client(timeout=15.0) as client:

            resp = client.get(

                f"{CALENDLY_API}/event_type_available_times",

                headers=_auth_headers(token),

                params={

                    "event_type": event_type_uri,

                    "start_time": start_utc,

                    "end_time": end_utc,

                },

            )

            resp.raise_for_status()

            return _parse_slot_collection(resp.json().get("collection", []), tz)

    except Exception as exc:

        logger.exception("Calendly availability check failed: %s", exc)

        return []





def fetch_upcoming_slots(

    token: str,

    event_type_uri: str,

    day_offset: Optional[int] = None,

    tz: ZoneInfo = DEFAULT_TZ,

    max_slots: int = 8,

) -> list[datetime]:

    """Fetch available slots for one day or the next 7 days."""

    if day_offset is not None:

        return fetch_available_slots(token, event_type_uri, day_offset=day_offset, tz=tz)[:max_slots]



    slots: list[datetime] = []

    for offset in range(7):

        day_slots = fetch_available_slots(token, event_type_uri, day_offset=offset, tz=tz)

        slots.extend(day_slots)

        if len(slots) >= max_slots:

            break

    return sorted(slots)[:max_slots]





def _parse_slot_collection(collection: list, tz: ZoneInfo) -> list[datetime]:

    slots: list[datetime] = []

    for item in collection:

        if item.get("status") != "available":

            continue

        start_str = item.get("start_time") or ""

        if not start_str.startswith("20"):

            continue

        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

        slots.append(dt.astimezone(tz))

    return sorted(slots)





def _format_time(dt: datetime) -> str:

    """Phone-friendly time, e.g. '2 PM'."""

    hour = dt.hour % 12 or 12

    ampm = "AM" if dt.hour < 12 else "PM"

    if dt.minute:

        return f"{hour}:{dt.minute:02d} {ampm}"

    return f"{hour} {ampm}"





def format_slot_speech(dt: datetime) -> str:

    """Spoken time for confirmations."""

    return _format_time(dt)





def format_day_label(dt: datetime) -> str:

    """Spoken day label like 'tomorrow' or 'Friday, June 27'."""

    now = datetime.now(dt.tzinfo or DEFAULT_TZ)

    if dt.date() == now.date():

        return "today"

    if dt.date() == (now + timedelta(days=1)).date():

        return "tomorrow"

    try:
        return dt.strftime("%A, %B %-d")
    except ValueError:
        return f"{dt.strftime('%A, %B')} {dt.day}"





def _format_slots(slots: list[datetime], day_label: str) -> str:

    if not slots:

        return (

            f"I'm sorry, there are no open appointments {day_label}. "

            "Would you like to try another day, or say schedule a demo to book a time?"

        )

    times = [_format_time(s) for s in slots[:4]]

    if len(slots) == 1:

        times_text = times[0]

    elif len(times) == 2:

        times_text = f"{times[0]} and {times[1]}"

    else:

        times_text = ", ".join(times[:-1]) + f", and {times[-1]}"

    extra = f" and {len(slots) - 4} more" if len(slots) > 4 else ""

    return (

        f"Yes, there are openings {day_label} at {times_text}{extra}. "

        "Would you like me to book one of those times for you?"

    )





def create_booking(

    token: str,

    event_type_uri: str,

    start_time_local: datetime,

    name: str,

    email: str,

    tz: ZoneInfo = DEFAULT_TZ,

) -> tuple[bool, str]:

    """

    Book a Calendly appointment via POST /invitees (Scheduling API).

    Returns (success, detail_or_error).

    """

    start_utc = start_time_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    body: dict = {

        "event_type": event_type_uri,

        "start_time": start_utc,

        "invitee": {

            "name": name,

            "email": email,

            "timezone": str(tz),

        },

    }



    details = get_event_type_details(token, event_type_uri)

    locations = details.get("locations") or []

    if locations:

        loc = locations[0]

        kind = loc.get("kind") or loc.get("type")

        if kind:

            body["location"] = {"kind": kind}



    try:

        with httpx.Client(timeout=20.0) as client:

            resp = client.post(

                f"{CALENDLY_API}/invitees",

                headers=_auth_headers(token),

                json=body,

            )

            if resp.status_code == 201:

                resource = resp.json().get("resource", {})

                uri = resource.get("uri", "booked")

                logger.info("Calendly booking created: %s for %s", uri, email)

                return True, uri

            error_body = resp.text[:300]

            logger.error("Calendly booking failed %s: %s", resp.status_code, error_body)

            return False, error_body

    except Exception as exc:

        logger.exception("Calendly booking request failed: %s", exc)

        return False, str(exc)





def check_availability_response(speech: str) -> Optional[str]:

    """

    If Calendly is configured, check real calendar slots and return a spoken answer.

    Returns None if not an availability question.

    """

    if not detect_availability_intent(speech):

        return None



    cfg = reload_settings()

    token = (cfg.calendly_api_token or "").strip()

    link = (cfg.calendly_link or "").strip()



    day_offset = _parse_target_day(speech)

    day_label = "today" if day_offset == 0 else "tomorrow"



    if not token or not link:

        return (

            f"I can't check the live calendar right now. "

            f"To see openings {day_label}, visit {calendly_spoken_hint(link)}, "

            "or say schedule a demo and I'll collect your information."

        )



    event_uri = (cfg.calendly_event_type_uri or "").strip() or resolve_event_type_uri(token, link)

    if not event_uri:

        return (

            "I'm having trouble reaching the calendar. "

            f"Please visit {calendly_spoken_hint(link)} to see available times, "

            "or say schedule a demo."

        )



    tz = ZoneInfo(cfg.calendly_timezone or "America/New_York")

    slots = fetch_available_slots(token, event_uri, day_offset=day_offset, tz=tz)

    logger.info("Calendly: %d slots found for %s", len(slots), day_label)

    return _format_slots(slots, day_label)





def availability_day_offset(speech: str) -> int:
    """Day offset when caller asks about availability (0=today, 1=tomorrow)."""
    return _parse_target_day(speech)


def should_start_live_booking(speech: str) -> bool:

    """True when caller wants to book after hearing availability or asks to schedule."""

    lowered = speech.lower()

    booking_phrases = (

        "book",

        "schedule",

        "yes",

        "yeah",

        "sure",

        "that works",

        "sounds good",

        "demo",

        "appointment",

        "set it up",

        "sign me up",

    )

    return any(phrase in lowered for phrase in booking_phrases)


