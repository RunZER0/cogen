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
    research_mode: str = "offline"
    specialist_mode: str = "orchestrated"
    live_specialist_limit: int = 5

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
