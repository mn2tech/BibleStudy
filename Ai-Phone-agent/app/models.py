"""Pydantic schemas and SQLAlchemy ORM models."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ---------------------------------------------------------------------------
# SQLAlchemy ORM
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class CallStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TRANSFERRED = "transferred"


class CallFlow(str, Enum):
    """Tracks what the caller is doing during the call."""

    NORMAL = "normal"
    TAKING_MESSAGE = "taking_message"
    TAKING_DEMO = "taking_demo"
    BOOKING_DEMO = "booking_demo"
    CANCELLING_APPOINTMENT = "cancelling_appointment"


class CallRecord(Base):
    """One row per incoming phone call."""

    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    caller_phone: Mapped[str] = mapped_column(String(32), default="unknown")
    status: Mapped[str] = mapped_column(String(32), default=CallStatus.IN_PROGRESS.value)
    flow: Mapped[str] = mapped_column(String(32), default=CallFlow.NORMAL.value)
    # JSON-ish state stored as text for demo/message collection steps
    flow_state: Mapped[str] = mapped_column(Text, default="{}")
    no_speech_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ConversationTurn(Base):
    """Each back-and-forth between caller and AI."""

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str] = mapped_column(String(64), index=True)
    caller_speech: Mapped[str] = mapped_column(Text, default="")
    ai_response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class VoicemailMessage(Base):
    """Messages left by callers who want a callback."""

    __tablename__ = "voicemail_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str] = mapped_column(String(64), index=True)
    caller_name: Mapped[str] = mapped_column(String(128), default="")
    caller_phone: Mapped[str] = mapped_column(String(32), default="")
    message_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DemoRequest(Base):
    """Demo / appointment requests collected during a call."""

    __tablename__ = "demo_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str] = mapped_column(String(64), index=True)
    caller_name: Mapped[str] = mapped_column(String(128), default="")
    business_name: Mapped[str] = mapped_column(String(128), default="")
    caller_phone: Mapped[str] = mapped_column(String(32), default="")
    preferred_time: Mapped[str] = mapped_column(String(256), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Pydantic API schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    database: str


class ConversationTurnOut(BaseModel):
    id: int
    caller_speech: str
    ai_response: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VoicemailOut(BaseModel):
    id: int
    caller_name: str
    caller_phone: str
    message_text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DemoRequestOut(BaseModel):
    id: int
    call_sid: str
    caller_name: str
    business_name: str
    caller_phone: str
    preferred_time: str
    email: str
    status: str
    calendly_link: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class DemoListResponse(BaseModel):
    demos: list[DemoRequestOut]
    total: int


class CallOut(BaseModel):
    id: int
    call_sid: str
    caller_phone: str
    status: str
    flow: str
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    turns: list[ConversationTurnOut] = Field(default_factory=list)
    messages: list[VoicemailOut] = Field(default_factory=list)
    demos: list[DemoRequestOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CallListItem(BaseModel):
    id: int
    call_sid: str
    caller_phone: str
    status: str
    summary: Optional[str] = None
    created_at: datetime
    turn_count: int = 0

    model_config = {"from_attributes": True}


class CallListResponse(BaseModel):
    calls: list[CallListItem]
    total: int
