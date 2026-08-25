from functools import lru_cache

from app.repository import PostgresRepository, SQLiteRepository, VentureRepository
from app.research import GeminiGroundedResearchProvider, OfflineResearchProvider, ResearchProvider
from app.service import VentureService
from app.settings import get_settings


@lru_cache
def get_repository() -> VentureRepository:
    settings = get_settings()
    backend = settings.database_backend.lower()
    if backend in {"postgres", "postgresql", "neon"}:
        if not settings.database_url:
            raise RuntimeError("DATABASE_BACKEND=postgres requires DATABASE_URL")
        return PostgresRepository(settings.database_url)
    if backend == "sqlite":
        return SQLiteRepository(settings.sqlite_path)
    raise RuntimeError(f"Unsupported DATABASE_BACKEND: {settings.database_backend}")


@lru_cache
def get_research_provider() -> ResearchProvider:
    settings = get_settings()
    if settings.research_mode.lower() == "live":
        if not settings.gemini_api_key:
            raise RuntimeError("RESEARCH_MODE=live requires GEMINI_API_KEY")
        return GeminiGroundedResearchProvider(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
        )
    return OfflineResearchProvider()


@lru_cache
def get_service() -> VentureService:
    return VentureService(get_repository(), get_research_provider())
