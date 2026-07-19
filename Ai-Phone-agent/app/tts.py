"""OpenAI text-to-speech for natural phone audio."""

import hashlib
import logging
import uuid

from openai import OpenAI

from app.config import reload_settings

logger = logging.getLogger(__name__)

# audio_id -> MP3 bytes (for Twilio <Play>)
_audio_cache: dict[str, bytes] = {}
# text hash -> audio_id (skip repeat TTS API calls)
_text_cache: dict[str, str] = {}


def _cache_key(text: str) -> str:
    cfg = reload_settings()
    raw = f"{cfg.openai_tts_model}:{cfg.openai_tts_voice}:{text.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_speech_audio(text: str) -> str | None:
    """
    Synthesize speech with OpenAI TTS and cache the MP3.

    Returns an audio ID for the /twilio/audio/{id} URL, or None to fall back to Twilio Say.
    """
    cfg = reload_settings()
    if cfg.voice_style.lower() in ("openai", "natural"):
        return None
    if not cfg.use_openai_tts or not cfg.openai_api_key:
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    key = _cache_key(cleaned)
    if key in _text_cache:
        cached_id = _text_cache[key]
        logger.info("TTS cache hit for %r", cleaned[:40])
        return cached_id

    try:
        client = OpenAI(api_key=cfg.openai_api_key)
        response = client.audio.speech.create(
            model=cfg.openai_tts_model,
            voice=cfg.openai_tts_voice,
            input=cleaned,
            response_format="mp3",
            speed=1.15,
        )
        audio_id = str(uuid.uuid4())
        _audio_cache[audio_id] = response.content
        _text_cache[key] = audio_id
        logger.info("OpenAI TTS generated %d bytes (%s)", len(response.content), audio_id[:8])
        return audio_id
    except Exception as exc:
        logger.exception("OpenAI TTS failed: %s", exc)
        return None


def prewarm_phrases(phrases: list[str]) -> None:
    """Generate TTS audio at startup so the first caller does not wait."""
    for phrase in phrases:
        if phrase.strip():
            generate_speech_audio(phrase)


def get_audio(audio_id: str) -> bytes | None:
    """Return cached MP3 bytes for Twilio to play."""
    return _audio_cache.get(audio_id)


def clear_audio(audio_id: str) -> None:
    """Remove cached audio after it has been played (optional cleanup)."""
    _audio_cache.pop(audio_id, None)
