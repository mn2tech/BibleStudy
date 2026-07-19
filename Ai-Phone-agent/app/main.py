"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.config import reload_settings
from app.database import (
    SessionLocal,
    count_turns,
    get_call_with_details,
    get_demo_request,
    get_demos_for_call,
    get_turns_for_call,
    get_voicemails_for_call,
    init_db,
    list_calls,
    list_demo_requests,
)
from app.models import (
    CallListItem,
    CallListResponse,
    CallOut,
    ConversationTurnOut,
    DemoListResponse,
    DemoRequestOut,
    HealthResponse,
    VoicemailOut,
)
from app.tts import generate_speech_audio, get_audio, prewarm_phrases
from app.twilio_routes import GREETING, PREWARM_PHRASES, router as twilio_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    init_db()
    cfg = reload_settings()
    if cfg.voice_style.lower() in ("openai", "natural") and cfg.use_openai_tts:
        prewarm_phrases(PREWARM_PHRASES)
    logger.info(
        "%s started (BASE_URL=%s, voice=%s)",
        cfg.app_name,
        cfg.base_url,
        cfg.voice_style,
    )
    yield
    logger.info("%s shutting down", cfg.app_name)


app = FastAPI(
    title=reload_settings().app_name,
    description="AI receptionist that answers Twilio voice calls using OpenAI.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(twilio_router)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_db():
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _demo_out(demo) -> DemoRequestOut:
    """Attach Calendly booking link from config to demo API responses."""
    out = DemoRequestOut.model_validate(demo)
    return out.model_copy(update={"calendly_link": reload_settings().calendly_link})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Simple health check for Railway/Render."""
    db_status = "connected"
    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        db_status = "error"

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        app=reload_settings().app_name,
        database=db_status,
    )


@app.get("/calls", response_model=CallListResponse)
async def get_calls(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> CallListResponse:
    """List recent calls with basic metadata."""
    calls, total = list_calls(db, limit=limit, offset=offset)
    items = [
        CallListItem(
            id=call.id,
            call_sid=call.call_sid,
            caller_phone=call.caller_phone,
            status=call.status,
            summary=call.summary,
            created_at=call.created_at,
            turn_count=count_turns(db, call.call_sid),
        )
        for call in calls
    ]
    return CallListResponse(calls=items, total=total)


@app.get("/calls/{call_sid}", response_model=CallOut)
async def get_call(
    call_sid: str,
    db: Session = Depends(get_db),
) -> CallOut:
    """Get full details for a single call including transcript and summary."""
    call = get_call_with_details(db, call_sid)
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call {call_sid} not found")

    turns = get_turns_for_call(db, call_sid)
    voicemails = get_voicemails_for_call(db, call_sid)
    demos = get_demos_for_call(db, call_sid)

    return CallOut(
        id=call.id,
        call_sid=call.call_sid,
        caller_phone=call.caller_phone,
        status=call.status,
        flow=call.flow,
        summary=call.summary,
        created_at=call.created_at,
        updated_at=call.updated_at,
        turns=[ConversationTurnOut.model_validate(t) for t in turns],
        messages=[VoicemailOut.model_validate(v) for v in voicemails],
        demos=[_demo_out(d) for d in demos],
    )


@app.get("/demos", response_model=DemoListResponse)
async def get_demos(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> DemoListResponse:
    """List all demo / appointment requests."""
    demos, total = list_demo_requests(db, limit=limit, offset=offset)
    return DemoListResponse(
        demos=[_demo_out(d) for d in demos],
        total=total,
    )


@app.get("/demos/{demo_id}", response_model=DemoRequestOut)
async def get_demo(
    demo_id: int,
    db: Session = Depends(get_db),
) -> DemoRequestOut:
    """Get a single demo request by ID."""
    demo = get_demo_request(db, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail=f"Demo request {demo_id} not found")
    return _demo_out(demo)


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
