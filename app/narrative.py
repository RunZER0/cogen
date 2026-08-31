"""One-shot LLM synthesis of a venture's current position into founder-facing prose.

Not a chat turn: the Position tab is read cold, with no prior conversation, so this has to stand
on its own — the same voice the agent already narrates with in chat, applied to the read surface
that used to be a dashboard of cards and numbers instead. Cached on the venture (see
UnderwritingResult.narrative / narrative_of) and only regenerated when the underwriting it was
built from has actually changed, so loading the tab is instant on every read after the first.
"""
from __future__ import annotations

import logging

from app.context import WorkingContextBuilder
from app.domain import SpecialistRole, Venture
from app.settings import get_settings

log = logging.getLogger(__name__)

_context_builder = WorkingContextBuilder()


def _rescue_line(venture: Venture) -> str:
    uw = venture.underwriting
    if not uw:
        return ""
    rescue = uw.rescue_candidate
    if not rescue:
        return "\nRESCUE CANDIDATE: none found — the engine could not identify a single lever that helps here."
    return (
        f"\nRESCUE CANDIDATE (already computed by the engine): shifting {rescue.assumption_label} "
        f"to {rescue.shocked_value} would move monthly operating profit to "
        f"{rescue.monthly_operating_profit_at_shock} (a gain of {rescue.gain}). Say plainly in your "
        f"narrative whether this has actually been tested in the sandbox yet (check for a matching "
        f"SandboxExperiment in context — if you cannot tell, say it hasn't been confirmed rather than "
        f"guessing) and what it showed, in the same prose voice as everything else — never a mechanical "
        f"tag like '(already tested)'."
    )


def _build_prompt(venture: Venture) -> str:
    context = _context_builder.build(
        venture, SpecialistRole.ADVERSARY, "narrate this venture's current position for the founder"
    )
    return f"""
You are Cogen, writing the narrative that leads the Position tab of a venture-underwriting
product — not a chat reply, a standalone account someone reads cold with no prior conversation.
Write the way a sharp, respectful partner would explain a real decision to someone about to risk
real money on it, not the way a dashboard displays one.

{context}
{_rescue_line(venture)}

Write 2-4 short paragraphs of plain prose. No headers, no bullet lists, no markdown tables, no
bold labels standing in for sentences. Lead with the decision and the one or two reasons that
actually drive it — not a recitation of every number on file. Weave the real figures in as
support for what you're saying, not as a separate ledger bolted on afterward. End with the single
most decision-relevant thing this venture still needs and why it matters, not a checklist. Never
state a fact, a figure, or a source that is not in the context above — an unstated gap is
something to name as a gap, not something to fill in.
""".strip()


async def generate_narrative(venture: Venture) -> str:
    """Return empty string (never raises) on any failure — a missing narrative just means the
    Position tab falls back to its existing structured cards, never a broken tab."""
    if not venture.underwriting:
        return ""
    settings = get_settings()
    try:
        if settings.research_provider.lower() == "openrouter":
            from app.llm import build_openrouter_client

            client = build_openrouter_client(settings)
            response = await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": _build_prompt(venture)}],
                max_tokens=900,
                extra_body={"provider": {"allow_fallbacks": True}, "reasoning": {"effort": "low"}},
            )
            return (response.choices[0].message.content or "").strip()

        if not settings.gemini_api_key:
            return ""
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        response = await client.aio.models.generate_content(
            model=settings.gemini_model, contents=_build_prompt(venture),
        )
        return (response.text or "").strip()
    except Exception:
        log.exception("Narrative synthesis failed for venture %s (non-fatal)", venture.id)
        return ""
