from functools import lru_cache

from app.repository import FirestoreRepository, SQLiteRepository, VentureRepository
from app.research import GeminiGroundedResearchProvider, OfflineResearchProvider, ResearchProvider
from app.service import VentureService
from app.settings import get_settings


@lru_cache
def get_repository() -> VentureRepository:
    settings = get_settings()
    if settings.database_backend.lower() == "firestore":
        return FirestoreRepository(
            project=settings.google_cloud_project,
            database=settings.firestore_database,
        )
    return SQLiteRepository(settings.sqlite_path)


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
