"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings for the AI phone agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 55
    openai_history_turns: int = 5
    use_openai_tts: bool = False
    openai_tts_model: str = "tts-1"
    openai_tts_voice: str = "shimmer"
    # twilio = instant Polly neural (default), openai = slow natural TTS, robotic = alice
    voice_style: str = "twilio"
    twilio_voice: str = "Polly.Joanna-Neural"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    transfer_phone_number: str = ""

    # Database — use sqlite:///./phone_agent.db locally, or a Postgres URL on Railway/Render
    database_url: str = "sqlite:///./phone_agent.db"

    # App
    app_name: str = "NM2TECH AI Phone Agent"
    debug: bool = False
    base_url: str = "http://localhost:8000"
    calendly_link: str = ""
    calendly_api_token: str = ""
    calendly_event_type_uri: str = ""
    calendly_timezone: str = "America/New_York"

    # Calendar — Google (recommended) or Calendly
    calendar_provider: str = ""  # google | calendly | auto
    calendar_timezone: str = "America/New_York"
    google_calendar_id: str = ""
    google_service_account_file: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    appointment_duration_minutes: int = 30
    business_hours_start: int = 9
    business_hours_end: int = 17

    # Twilio speech settings
    gather_timeout: int = 120  # max wait for caller speech (must cover long TTS + barge-in)
    speech_timeout: str = "auto"  # auto = end at first pause; better for interrupts than a fixed 2s
    gather_max_speech_time: int = 30  # max seconds of caller speech per turn
    speech_language: str = "en-US"
    twilio_barge_in: bool = True  # let caller interrupt TTS by speaking
    gather_hints: str = (
        "appointment, demo, cancel, book, schedule, transfer, message, yes, no, never mind"
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def reload_settings() -> Settings:
    """Reload settings from .env — call after updating environment variables."""
    get_settings.cache_clear()
    return get_settings()
