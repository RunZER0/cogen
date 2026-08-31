"""Google Places API (New) integration, independent of the specialist research pipeline.

Same relationship to `app.agent_tools` that `app.websearch` has: a leaf module the chat agent calls
directly for one specific kind of lookup — real, named local businesses near an actual address —
that a general web search answers only vaguely. `search_web` finds a blog post claiming "Reno has
several pet groomers"; this finds the actual groomers, their addresses, and their ratings.
"""
from __future__ import annotations

from typing import Any

from app.settings import get_settings

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,"
    "places.businessStatus,places.googleMapsUri"
)


def find_nearby_places(query: str, *, max_results: int = 10) -> list[dict[str, Any]]:
    """Text-search Google Places for real local businesses and return structured results.

    `query` should name both the business type and the place, e.g. "pet grooming salons in Reno,
    Nevada" or "laptop repair shops near downtown Dallas, TX" — Places resolves the location from
    the text itself, no separate geocoding step needed.

    Raises RuntimeError if no Maps API key is configured, so the caller can tell "no results" apart
    from "this capability is not available in this environment" — the agent should fall back to
    search_web on that specific failure, not report a blank finding.
    """
    import httpx

    settings = get_settings()
    if not settings.google_maps_api_key:
        raise RuntimeError("Local business search requires GOOGLE_MAPS_API_KEY")

    response = httpx.post(
        _SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_maps_api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        },
        json={"textQuery": query, "maxResultCount": max(1, min(max_results, 20))},
        timeout=20.0,
    )
    response.raise_for_status()
    data = response.json()
    places = data.get("places") or []
    if not isinstance(places, list):
        raise ValueError("Places API response contained invalid results")
    return [
        {
            "name": (place.get("displayName") or {}).get("text", ""),
            "address": place.get("formattedAddress", ""),
            "rating": place.get("rating"),
            "rating_count": place.get("userRatingCount"),
            "status": place.get("businessStatus"),
            "maps_url": place.get("googleMapsUri", ""),
        }
        for place in places
        if isinstance(place, dict) and (place.get("displayName") or {}).get("text")
    ]
