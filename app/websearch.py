"""General-purpose web search tool, independent of the specialist research pipeline.

`app/research.py`'s Tavily integration is bound to one venture/role/mandate and feeds the deterministic
evidence ledger. This module is the same retrieval call used unscoped — for the chat agent to find
candidate competitors, suppliers, or professionals before deciding what to look at in detail with
`app.browser.browse_page`. Results returned here are candidates to investigate, not evidence; nothing
here writes to venture state on its own.
"""
from __future__ import annotations

from typing import Any

from app.settings import get_settings


def search_web(query: str, *, max_results: int = 6) -> list[dict[str, Any]]:
    """Search the live web via Tavily and return [{title, url, content}, ...].

    Raises RuntimeError if no Tavily key is configured, so the caller can tell "no results" apart
    from "search is not available in this environment".
    """
    import httpx

    settings = get_settings()
    if not settings.tavily_api_key:
        raise RuntimeError("Web search requires TAVILY_API_KEY")

    response = httpx.post(
        f"{settings.tavily_base_url.rstrip('/')}/search",
        headers={
            "Authorization": f"Bearer {settings.tavily_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": max(1, min(max_results, 10)),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    results = data.get("results") or []
    if not isinstance(results, list):
        raise ValueError("Tavily search response contained invalid results")
    return [
        {"title": item.get("title", ""), "url": item.get("url", ""), "content": item.get("content", "")}
        for item in results
        if isinstance(item, dict) and item.get("url")
    ]
