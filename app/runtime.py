from functools import lru_cache

from app.agentic_research import AgenticSpecialistResearchProvider
from app.repository import PostgresRepository, SQLiteRepository, VentureRepository
from app.research import (
    GeminiGroundedResearchProvider,
    OfflineResearchProvider,
    OpenRouterGroundedResearchProvider,
    ResearchProvider,
)
from app.service import VentureService
from app.settings import get_settings
from app.state import StateStore
from app.subagents import SubagentRegistry


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
        provider = settings.research_provider.lower()
        if settings.specialist_mode.lower() == "agentic":
            # Real ADK sub-agents (one per specialist role) with live search/browse tools, instead of
            # one non-agentic completion reading static search snippets. Reuses the same
            # RESEARCH_PROVIDER (openrouter/gemini) for the underlying model and the same admissibility
            # gate downstream — this only changes how a specialist's candidate findings get produced,
            # not what is allowed to become evidence. SPECIALIST_MODE=orchestrated (the default) keeps
            # the prior, already-tested single-completion providers below untouched.
            if provider == "openrouter" and not settings.openrouter_api_key:
                raise RuntimeError("SPECIALIST_MODE=agentic with RESEARCH_PROVIDER=openrouter requires OPENROUTER_API_KEY")
            if provider == "gemini" and not (settings.gemini_api_key or settings.google_genai_use_vertexai):
                raise RuntimeError("SPECIALIST_MODE=agentic with RESEARCH_PROVIDER=gemini requires GEMINI_API_KEY or Vertex AI")
            if not settings.tavily_api_key:
                raise RuntimeError("SPECIALIST_MODE=agentic requires TAVILY_API_KEY for the search_web tool")
            return AgenticSpecialistResearchProvider(settings)
        if provider == "openrouter":
            if not settings.openrouter_api_key:
                raise RuntimeError(
                    "RESEARCH_MODE=live with RESEARCH_PROVIDER=openrouter requires OPENROUTER_API_KEY"
                )
            if not settings.tavily_api_key:
                raise RuntimeError(
                    "RESEARCH_MODE=live with RESEARCH_PROVIDER=openrouter requires TAVILY_API_KEY"
                )
            return OpenRouterGroundedResearchProvider(
                model=settings.openrouter_model,
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                tavily_api_key=settings.tavily_api_key,
                tavily_base_url=settings.tavily_base_url,
            )
        if provider != "gemini":
            raise RuntimeError(f"Unsupported RESEARCH_PROVIDER: {settings.research_provider}")
        if not (settings.gemini_api_key or settings.google_genai_use_vertexai):
            raise RuntimeError("RESEARCH_MODE=live with RESEARCH_PROVIDER=gemini requires GEMINI_API_KEY or Vertex AI")
        return GeminiGroundedResearchProvider(
            model=settings.gemini_model,
            api_key=None if settings.google_genai_use_vertexai else settings.gemini_api_key,
            fallback_model=settings.gemini_fallback_model,
            attempts_per_model=settings.gemini_attempts_per_model,
        )
    return OfflineResearchProvider()


@lru_cache
def get_subagent_registry() -> SubagentRegistry:
    """Singleton runtime for sandbox/specialist subagent runs. Deliberately built from its own
    StateStore(get_repository()) rather than get_service().state: get_service() below wires this
    registry INTO the VentureService it constructs (so WorkflowRunner can use it), so depending on
    get_service() here would be circular. A second StateStore is just a stateless facade over the
    same repository singleton — safe to have more than one."""
    return SubagentRegistry(StateStore(get_repository()))


@lru_cache
def get_service() -> VentureService:
    settings = get_settings()
    return VentureService(
        get_repository(),
        get_research_provider(),
        specialist_research_rounds=settings.specialist_research_rounds,
        subagent_registry=get_subagent_registry(),
    )
