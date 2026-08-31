from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080

    database_backend: str = "sqlite"
    sqlite_path: str = "./venture_twin.db"
    database_url: str | None = None

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.7-flash"
    gemini_fallback_model: str | None = "gemini-3.6-flash"
    gemini_attempts_per_model: int = 2
    google_genai_use_vertexai: bool = False
    research_provider: str = "gemini"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-3.5-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    tavily_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "TAVILY"),
    )
    tavily_base_url: str = "https://api.tavily.com"
    research_mode: str = "offline"
    specialist_mode: str = "orchestrated"
    live_specialist_limit: int = 5
    specialist_research_rounds: int = 2
    # How often the in-process monitor cron wakes up to check all due schedules.
    monitor_interval_seconds: int = 3600

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_maps_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
