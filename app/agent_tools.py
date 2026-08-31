"""Shared ADK tool wrappers for live web research.

A leaf module — depends only on `app.websearch` and `app.browser` — so both the conversational root
agent and the specialist research agents can call the exact same tools without a circular import
back through `app.agent` (which itself sits behind `app.runtime` → `app.service` → `app.orchestration`).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
from typing import Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., str])


def as_async_tool(fn: _F) -> _F:
    """Wrap a synchronous tool in a real coroutine that runs it on a worker thread.

    Synchronous ADK tools in this app touch Postgres (psycopg, sync) or, for
    search_web/browse_page_for_details, real network/Playwright I/O that can run for seconds.
    ADK's function-calling for the ordinary (non-Live) Runner.run_async path this app uses invokes
    a sync tool inline on the caller's own coroutine — verified by reading ADK's source: the only
    place ADK offers to run a sync tool in a thread pool (RunConfig.tool_thread_pool_config) is
    read exclusively inside the Live-API function-calling helper, never the regular flow.
    Confirmed live, twice: with tools left unwrapped, ONE browse_page_for_details call froze the
    entire server — every request on the process, including a bare /readyz — for the length of
    that one call, and adding the Live-only RunConfig option changed nothing, because that flow
    never reads it. Making the tool itself a genuine `async def` that awaits asyncio.to_thread is
    what ADK's own _invoke_callable already awaits directly (it only special-cases sync
    callables), so this is the actual fix rather than routing through a config ADK does not
    consult here. functools.wraps preserves the original signature/docstring, which is what ADK's
    schema builder reads to construct the tool's declaration — verified this produces an
    identical signature via inspect.signature.
    """

    if inspect.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def search_web(query: str) -> str:
    """Search the live web for candidates: competitors, suppliers, professionals, listings, pricing.

    Returns titles, URLs and short excerpts, not full page content. Use browse_page_for_details on a
    specific promising result to read its actual content — a search snippet is not evidence on its own.
    """
    from app.websearch import search_web as _search_web

    try:
        return json.dumps(_search_web(query), indent=2)
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})


def find_local_competitors(query: str) -> str:
    """Find real, named local businesses via Google Places — actual competitors with addresses and
    ratings, not a vague web-search estimate of how many might exist.

    Name both the business type and the place in the query, e.g. "pet grooming salons in Reno,
    Nevada" or "laptop repair shops near downtown Dallas, TX". Use this for competitor density and
    identifying specific nearby competitors by name; use search_web for anything that is not a
    place (pricing benchmarks, regulations, suppliers). If this returns an error because Maps is
    not configured in this environment, fall back to search_web instead of leaving the assumption
    blank.
    """
    from app.places import find_nearby_places

    try:
        return json.dumps(find_nearby_places(query), indent=2)
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})


def browse_page_for_details(url: str) -> str:
    """Render one specific URL (JavaScript included) and return its visible text.

    Use this to read an actual competitor menu/pricing page, a supplier's quote page, or a professional's
    listing that a search snippet did not fully capture. Fetches exactly one page; never submits forms,
    clicks through a flow, or logs in. Only public http/https addresses are reachable — internal or
    private addresses are refused.
    """
    from app.browser import browse_page

    return json.dumps(browse_page(url), indent=2)
