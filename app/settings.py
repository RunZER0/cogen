from functools import lru_cache

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
    research_mode: str = "offline"
    specialist_mode: str = "orchestrated"
    live_specialist_limit: int = 5
    specialist_research_rounds: int = 2

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
