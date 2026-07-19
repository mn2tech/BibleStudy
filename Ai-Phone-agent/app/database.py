"""Database setup and helper functions."""

import json
import logging
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import (
    Base,
    CallFlow,
    CallRecord,
    CallStatus,
    ConversationTurn,
    DemoRequest,
    VoicemailMessage,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# SQLite needs check_same_thread=False for FastAPI async workers
connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized at %s", settings.database_url.split("@")[-1])


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_or_create_call(session: Session, call_sid: str, caller_phone: str = "unknown") -> CallRecord:
    """Fetch an existing call or create a new one."""
    call = session.execute(
        select(CallRecord).where(CallRecord.call_sid == call_sid)
    ).scalar_one_or_none()

    if call is None:
        call = CallRecord(call_sid=call_sid, caller_phone=caller_phone or "unknown")
        session.add(call)
        session.flush()
        logger.info("Created call record for %s from %s", call_sid, caller_phone)

    return call


def save_conversation_turn(
    session: Session,
    call_sid: str,
    caller_speech: str,
    ai_response: str,
) -> ConversationTurn:
    """Persist one conversation turn."""
    turn = ConversationTurn(
        call_sid=call_sid,
        caller_speech=caller_speech,
        ai_response=ai_response,
    )
    session.add(turn)
    session.flush()
    return turn


def save_voicemail(
    session: Session,
    call_sid: str,
    caller_name: str,
    caller_phone: str,
    message_text: str,
) -> VoicemailMessage:
    """Save a voicemail / callback message."""
    msg = VoicemailMessage(
        call_sid=call_sid,
        caller_name=caller_name,
        caller_phone=caller_phone,
        message_text=message_text,
    )
    session.add(msg)
    session.flush()
    return msg


def save_demo_request(
    session: Session,
    call_sid: str,
    caller_name: str,
    business_name: str,
    caller_phone: str,
    preferred_time: str,
    email: str = "",
    status: str = "pending",
) -> DemoRequest:
    """Save a demo / appointment request from a completed collection flow."""
    demo = DemoRequest(
        call_sid=call_sid,
        caller_name=caller_name,
        business_name=business_name,
        caller_phone=caller_phone,
        preferred_time=preferred_time,
        email=email,
        status=status,
    )
    session.add(demo)
    session.flush()
    logger.info(
        "Demo request saved: %s / %s / %s",
        caller_name,
        business_name,
        preferred_time,
    )
    return demo


def get_flow_state(call: CallRecord) -> dict:
    """Parse JSON flow state from the call record."""
    try:
        return json.loads(call.flow_state or "{}")
    except json.JSONDecodeError:
        return {}


def set_flow_state(call: CallRecord, state: dict) -> None:
    """Serialize flow state back onto the call record."""
    call.flow_state = json.dumps(state)


def get_call_with_details(session: Session, call_sid: str) -> Optional[CallRecord]:
    """Fetch a single call by SID."""
    return session.execute(
        select(CallRecord).where(CallRecord.call_sid == call_sid)
    ).scalar_one_or_none()


def list_calls(session: Session, limit: int = 50, offset: int = 0) -> tuple[list[CallRecord], int]:
    """Return paginated call records."""
    total = session.query(CallRecord).count()
    calls = (
        session.query(CallRecord)
        .order_by(CallRecord.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return calls, total


def count_turns(session: Session, call_sid: str) -> int:
    """Count conversation turns for a call."""
    return session.query(ConversationTurn).filter(ConversationTurn.call_sid == call_sid).count()


def get_turns_for_call(session: Session, call_sid: str) -> list[ConversationTurn]:
    """Return all turns for a call, oldest first."""
    return (
        session.query(ConversationTurn)
        .filter(ConversationTurn.call_sid == call_sid)
        .order_by(ConversationTurn.created_at.asc())
        .all()
    )


def get_voicemails_for_call(session: Session, call_sid: str) -> list[VoicemailMessage]:
    """Return voicemail messages linked to a call."""
    return (
        session.query(VoicemailMessage)
        .filter(VoicemailMessage.call_sid == call_sid)
        .order_by(VoicemailMessage.created_at.asc())
        .all()
    )


def get_demos_for_call(session: Session, call_sid: str) -> list[DemoRequest]:
    """Return demo requests linked to a call."""
    return (
        session.query(DemoRequest)
        .filter(DemoRequest.call_sid == call_sid)
        .order_by(DemoRequest.created_at.asc())
        .all()
    )


def list_demo_requests(
    session: Session, limit: int = 50, offset: int = 0
) -> tuple[list[DemoRequest], int]:
    """Return paginated demo requests, newest first."""
    total = session.query(DemoRequest).count()
    demos = (
        session.query(DemoRequest)
        .order_by(DemoRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return demos, total


def get_demo_request(session: Session, demo_id: int) -> Optional[DemoRequest]:
    """Fetch a single demo request by ID."""
    return session.query(DemoRequest).filter(DemoRequest.id == demo_id).first()


def cancel_latest_booking_for_phone(session: Session, caller_phone: str) -> bool:
    """Mark the most recent booked demo for this phone as cancelled."""
    demo = (
        session.query(DemoRequest)
        .filter(DemoRequest.caller_phone == caller_phone, DemoRequest.status == "booked")
        .order_by(DemoRequest.created_at.desc())
        .first()
    )
    if demo is None:
        return False
    demo.status = "cancelled"
    session.flush()
    logger.info("Demo request %s marked cancelled for %s", demo.id, caller_phone)
    return True


def mark_call_completed(session: Session, call: CallRecord, status: str = CallStatus.COMPLETED.value) -> None:
    """Update call status when the call ends."""
    call.status = status
    session.flush()
