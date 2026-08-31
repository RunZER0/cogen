"""Shared ADK model construction.

A leaf module — depends only on `app.settings` and third-party SDKs — so both the conversational
root agent (`app/agent.py`) and the specialist research agents (`app/agentic_research.py`) can build
an identical, already-tuned model without either module importing the other. Importing this from
`app/orchestration.py`'s dependency chain (which reaches back through `app.service` and `app.runtime`)
would deadlock into a circular import if it lived in `app/agent.py` instead.
"""
from __future__ import annotations

import logging

from app.settings import Settings

log = logging.getLogger(__name__)


def build_openrouter_client(settings: Settings):
    """The same patched AsyncOpenAI client build_agent_model wraps for ADK — exposed separately
    so a plain one-shot completion (app/narrative.py's Position-tab synthesis, for instance) gets
    the same reasoning-effort bound and safety-filter-fragment fix without duplicating either."""
    if not settings.openrouter_api_key:
        raise RuntimeError("RESEARCH_PROVIDER=openrouter requires OPENROUTER_API_KEY")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_retries=2,
        timeout=180.0,
    )
    original_create = client.chat.completions.create

    async def create_with_openrouter_fallback(*args, **kwargs):
        extra_body = dict(kwargs.pop("extra_body", {}) or {})
        extra_body.setdefault("provider", {"allow_fallbacks": True})
        # This model bills hidden chain-of-thought as its own "internal_reasoning" token category,
        # drawn from the same completion budget as the visible answer. Verified against a live
        # multi-tool due-diligence turn: with reasoning uncapped, a 4096-token budget was consumed
        # entirely by hidden reasoning after two search_web calls and one browse_page_for_details,
        # producing zero visible output and no error — it looked like the agent silently gave up.
        # Bounding effort leaves the budget for the answer it exists to produce.
        extra_body.setdefault("reasoning", {"effort": "low"})
        response = await original_create(*args, extra_body=extra_body, **kwargs)
        # DEBUG, not WARNING — this fires on every call, so it stays silent at the default
        # level. Turn on app.llm at DEBUG when chasing a truncated/empty-response class of bug
        # (the reasoning-token trap above is one instance of it) to see finish_reason and
        # whether the budget went to a tool call, visible text, or neither.
        if not kwargs.get("stream") and getattr(response, "choices", None):
            choice = response.choices[0]
            log.debug(
                "openrouter finish_reason=%s usage=%s tool_calls=%s content_len=%s",
                choice.finish_reason,
                getattr(response, "usage", None),
                bool(getattr(choice.message, "tool_calls", None)),
                len(choice.message.content or ""),
            )
            # Verified live: Gemini's own safety classifier can trip on a refusal-worthy
            # request (asked to help structure cash to dodge IRS reporting) and cut generation
            # at zero-to-a-few tokens — finish_reason="content_filter" (native_finish_reason
            # "SAFETY"), content left None or a bare fragment like "I cannot" — before the
            # model gets to actually explain anything. Left alone, main.py's chat loop treats
            # that fragment as a genuine final answer (or, if content is empty, burns through
            # its retry budget re-asking the same already-filtered request, which only ever
            # produces the same cut). Neither serves the founder: substituting one clear,
            # honest decline here means a safety-filtered turn always reads as an intentional,
            # complete answer instead of a broken one — and skips the pointless retries, since
            # asking the identical filtered request again cannot produce a different result.
            if (
                str(choice.finish_reason or "").lower() == "content_filter"
                and not getattr(choice.message, "tool_calls", None)
            ):
                log.info(
                    "OpenRouter safety-filtered a response (native_finish_reason=%s) — "
                    "substituting a complete decline instead of a truncated fragment.",
                    getattr(response, "model_extra", {}).get("native_finish_reason")
                    if hasattr(response, "model_extra") else None,
                )
                choice.message.content = (
                    "I can't help with that — the request itself triggered a content-safety "
                    "stop before I could generate a real response, which happens regardless of "
                    "how it's framed. If this was a legitimate ask that got caught by mistake, "
                    "try rephrasing it plainly; otherwise I'm glad to keep working on the "
                    "venture itself on straightforward terms."
                )
        return response

    client.chat.completions.create = create_with_openrouter_fallback
    return client


def build_agent_model(settings: Settings):
    """Construct the LLM backing an ADK agent, honoring settings.research_provider."""
    if settings.research_provider.lower() == "openrouter":
        from google.adk.labs.openai._openai_llm import OpenAILlm

        client = build_openrouter_client(settings)
        return OpenAILlm(
            model=settings.openrouter_model,
            # 1200 was enough for a short structured-JSON-only flow. Real due diligence chains
            # search_web, browse_page_for_details, and reasoning in one turn, so it needs real
            # headroom even with reasoning effort bounded above. Verified live at 6000 still cut off
            # mid-task on a dense multi-fact message. This model's completion ceiling is 65536 per
            # OpenRouter, so there is plenty of room.
            max_tokens=12000,
            client=client,
        )
    if not settings.gemini_api_key:
        raise RuntimeError("RESEARCH_PROVIDER=gemini requires GEMINI_API_KEY")
    from google.adk.models import Gemini
    from google.genai import types

    return Gemini(
        model=settings.gemini_model,
        # Verified live: without this, the underlying google.genai Client looks for the key in the
        # process's real OS environment, not in our Settings object — GEMINI_API_KEY loaded from
        # .env via pydantic-settings never reaches os.environ, so a chat call failed with "No API key
        # was provided" even though get_settings().gemini_api_key was correctly set. client_kwargs is
        # ADK's documented passthrough to the google.genai.Client constructor.
        client_kwargs={"api_key": settings.gemini_api_key},
        retry_options=types.HttpRetryOptions(attempts=3),
    )
