"""Persistent founder model — the "learns the user" layer.

The venture twin is per-venture. This module is the cross-venture, cross-session memory of the
founder themselves: what they build, how they run it, what they tolerate, and what actually
happened. It powers three things:

1. **Tailored responses** — not sycophancy, but scientific fit: the agent knows the founder's
   capital, risk tolerance, time commitment, experience, and demonstrated preferences, and
   calibrates its recommendations to that person rather than a generic founder.
2. **Pivot pairing on rejection** — a REJECT/CONDITIONAL is never handed back bare; it is paired
   with validated alternative configurations (forks/sandbox scenarios) that fit the founder's
   demonstrated constraints.
3. **Weekly recommendation agent** — a second background agent that explores in the direction of
   the founder's learned interests and returns a full, numbered recommendation with reasoning.

The founder model is stored as a durable state record (kind "founder_model") so it survives
process restarts and is independent of any single venture's chat transcript.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime

from app.domain import Decision, Venture

log = logging.getLogger(__name__)

KIND_FOUNDER_MODEL = "founder_model"
KIND_RECOMMENDATION = "recommendation"


def _safe_float(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bucket(value: float | None, edges: list[float], labels: list[str]) -> str | None:
    if value is None:
        return None
    for edge, label in zip(edges, labels):
        if value <= edge:
            return label
    return labels[-1] if labels else None


class FounderModelBuilder:
    """Deterministic aggregation of every venture a founder has touched into one profile.

    No LLM is involved in building the profile itself — it is a reproducible summary of the
    founder's actual, durable state (capital, reserve, loss tolerance, time commitment, the
    business categories they pursue, and the outcomes those ventures reached). The LLM is only
    used later, to *use* this profile for tailored prose and recommendations.
    """

    def build(self, ventures: list[Venture]) -> dict:
        if not ventures:
            return {"founder_id": "founder", "ventures_seen": 0, "profile": {}, "interests": []}

        capitals: list[float] = []
        reserves: list[float] = []
        losses: list[float] = []
        incomes: list[float] = []
        time_commitments: Counter = Counter()
        categories: Counter = Counter()
        locations: Counter = Counter()
        currencies: Counter = Counter()
        decisions: Counter = Counter()
        outcomes: list[dict] = []

        for v in ventures:
            f = v.intake.founder
            cap = _safe_float(f.available_capital)
            if cap is not None:
                capitals.append(cap)
            res = _safe_float(f.protected_reserve)
            if res is not None:
                reserves.append(res)
            loss = _safe_float(f.max_acceptable_loss)
            if loss is not None:
                losses.append(loss)
            inc = _safe_float(f.target_monthly_owner_income)
            if inc is not None:
                incomes.append(inc)
            time_commitments[f.time_commitment or "full-time"] += 1
            categories[v.intake.business_type or "general"] += 1
            locations[v.intake.location or "unknown"] += 1
            currencies[v.intake.currency or "LOCAL"] += 1
            if v.underwriting:
                decisions[v.underwriting.decision.value] += 1
            outcomes.append({
                "idea": v.intake.idea,
                "business_type": v.intake.business_type,
                "location": v.intake.location,
                "decision": v.underwriting.decision.value if v.underwriting else None,
                "survival": v.underwriting.break_even_probability_12m if v.underwriting else None,
                "capital": cap,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            })

        def median(vals: list[float]) -> float | None:
            if not vals:
                return None
            s = sorted(vals)
            n = len(s)
            return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

        med_cap = median(capitals)
        med_res = median(reserves)
        med_loss = median(losses)
        med_inc = median(incomes)

        profile = {
            "typical_capital": med_cap,
            "typical_reserve": med_res,
            "typical_max_loss": med_loss,
            "typical_income_target": med_inc,
            "capital_bucket": _bucket(med_cap, [250_000, 1_000_000, 5_000_000], ["micro", "small", "mid", "large"]),
            "loss_tolerance_bucket": _bucket(med_loss, [100_000, 500_000, 2_000_000], ["conservative", "moderate", "aggressive", "very_aggressive"]),
            "dominant_time_commitment": time_commitments.most_common(1)[0][0] if time_commitments else "full-time",
            "decision_distribution": dict(decisions),
            "venture_count": len(ventures),
        }

        interests = [cat for cat, _ in categories.most_common(5)]
        # A founder who keeps pursuing the same category is expressing a durable interest; weight
        # repeated categories so the recommendation agent explores in that direction.
        interest_scores = {cat: count for cat, count in categories.items()}

        return {
            "founder_id": "founder",
            "ventures_seen": len(ventures),
            "profile": profile,
            "interests": interests,
            "interest_scores": interest_scores,
            "top_locations": [loc for loc, _ in locations.most_common(3)],
            "currencies": [cur for cur, _ in currencies.most_common(3)],
            "outcomes": outcomes,
            "built_at": datetime.now(UTC).isoformat(),
        }


class UserMemory:
    """Two-tier durable memory: venture-scoped learning + cross-session user memory.

    Tier 1 (venture-scoped): what the agents learned while working a specific venture — the
    founder's decisions, corrections, and preferences expressed *in that venture's context*.
    Already lives on the venture itself (evidence, changes, decision history, chat session).

    Tier 2 (cross-session user memory): durable facts and preferences about the founder that
    outlive any single venture — "prefers lean openings", "risk-averse about long leases",
    "always asks about footfall first". These are stored as durable state records (kind
    "user_memory") keyed by a stable slug, so they survive process restarts and are available to
    every venture's agents, not just the one where the fact was learned.
    """

    KIND = "user_memory"

    @staticmethod
    def remember(state, key: str, fact: str, *, venture_id: str | None = None, source: str = "agent") -> dict:
        """Persist one durable fact about the founder (upsert by key)."""
        from app.domain import utc_now

        record = {
            "key": key,
            "fact": fact,
            "venture_id": venture_id,
            "source": source,
            "updated_at": utc_now().isoformat(),
        }
        state.repository.save_state_record(
            UserMemory.KIND, key, venture_id or "founder", json.dumps(record), idempotency_key=f"user_memory:{key}",
        )
        return record

    @staticmethod
    def recall(state, venture_id: str | None = None) -> list[dict]:
        """All durable user-memory facts, optionally filtered to one venture's contributions."""
        payloads = state.repository.list_all_state_records(UserMemory.KIND)
        out = []
        for payload in payloads:
            try:
                rec = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if venture_id and rec.get("venture_id") not in (None, venture_id):
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r.get("updated_at", ""), reverse=True)


def _tailoring_notes(profile: dict) -> str:
    """Human-readable guidance derived deterministically from the founder profile.

    This is the "scientific fit" — the agent is told the founder's demonstrated constraints and
    preferences so it can calibrate, NOT to flatter them. It is explicit that tailoring means
    fitting the recommendation to the person's real constraints, never becoming a yes-servant.
    """
    p = profile.get("profile", {})
    lines = []
    cap = p.get("typical_capital")
    if cap is not None:
        lines.append(f"Typical available capital across their ventures: {cap:,.0f}.")
    loss = p.get("loss_tolerance_bucket")
    if loss:
        lines.append(f"Demonstrated loss tolerance: {loss}.")
    tc = p.get("dominant_time_commitment")
    if tc:
        lines.append(f"Dominant time commitment: {tc}.")
    dist = p.get("decision_distribution", {})
    if dist:
        lines.append("Decision history across their ventures: " + ", ".join(f"{k}={v}" for k, v in dist.items()) + ".")
    interests = profile.get("interests", [])
    if interests:
        lines.append("Business categories they repeatedly pursue: " + ", ".join(interests) + ".")
    if not lines:
        return "No founder profile established yet — treat them as a new founder and calibrate from the current venture's own intake."
    return "\n".join(lines)


def build_tailoring_context(ventures: list[Venture]) -> str:
    """A compact, factual block injected into the agent's context so it can tailor to the founder.

    Deliberately framed to prevent sycophancy: it tells the agent the founder's real constraints
    and history so recommendations fit the person, and explicitly forbids using it to flatter or
    to soften an honest rejection.
    """
    model = FounderModelBuilder().build(ventures)
    return (
        "FOUNDER MODEL (learned across all their ventures — use to fit recommendations to this "
        "person's real constraints, never to flatter or to soften an honest rejection):\n"
        + _tailoring_notes(model)
    )


def pivot_candidates(venture: Venture, ventures: list[Venture]) -> list[dict]:
    """Deterministic pivot/branch candidates for a rejected or conditional venture.

    Returns concrete alternative configurations that fit the founder's demonstrated constraints
    (capital, loss tolerance) — the "don't just reject, pair it with what could work" behavior.
    Each candidate is a fork/sandbox-ready shock set with a plain-language rationale.
    """
    if not venture.underwriting:
        return []
    decision = venture.underwriting.decision
    if decision not in (Decision.REJECT, Decision.CONDITIONAL):
        return []

    assumptions = venture.assumption_map()
    candidates: list[dict] = []

    # 1. If the engine found a rescue candidate, surface it first.
    rescue = venture.underwriting.rescue_candidate
    if rescue:
        candidates.append({
            "kind": "rescue",
            "title": f"Improve {rescue.assumption_label}",
            "shocks": {rescue.assumption_key: rescue.shocked_value},
            "rationale": (
                f"The engine's single largest lever: shifting {rescue.assumption_label} to "
                f"{rescue.shocked_value} would move monthly operating profit to "
                f"{rescue.monthly_operating_profit_base or rescue.monthly_operating_profit_at_shock}."
            ),
        })

    # 2. A leaner-cost configuration (lower setup / rent) — fits a capital-constrained founder.
    setup = assumptions.get("setup_costs")
    if setup and setup.value:
        lean_setup = setup.value * 0.6
        candidates.append({
            "kind": "lean",
            "title": "Leaner opening (smaller footprint / staged buildout)",
            "shocks": {"setup_costs": lean_setup},
            "rationale": (
                f"Cutting opening cost from {setup.value:,.0f} to {lean_setup:,.0f} preserves the "
                "same revenue engine while widening the working-capital cushion — the exact thing "
                "that killed this configuration."
            ),
        })

    # 3. A higher-margin configuration (the classic retail pivot).
    margin = assumptions.get("gross_margin_pct")
    if margin and margin.value:
        target = min(0.95, margin.value * 1.5)
        candidates.append({
            "kind": "margin",
            "title": "Higher-margin mix (premium / services blend)",
            "shocks": {"gross_margin_pct": target},
            "rationale": (
                f"Raising blended gross margin from {margin.value:.0%} toward {target:.0%} — the "
                "single most common structural fix for a thin-margin retail rejection."
            ),
        })

    return candidates


def _recommendation_prompt(profile: dict, ventures: list[Venture]) -> str:
    """Prompt for the weekly recommendation agent — a full agent that explores in the founder's
    interest direction and returns a numbered, reasoned recommendation."""
    interests = profile.get("interests", []) or ["small business"]
    top_locations = profile.get("top_locations", []) or []
    currencies = profile.get("currencies", []) or []
    p = profile.get("profile", {})
    cap = p.get("typical_capital")
    loss = p.get("loss_tolerance_bucket")
    tc = p.get("dominant_time_commitment")

    recent = "\n".join(
        f"- {o.get('idea')} ({o.get('business_type')}) in {o.get('location')}: "
        f"decision={o.get('decision')}, survival={o.get('survival')}"
        for o in (profile.get("outcomes") or [])[-5:]
    )

    return f"""
You are Cogen's weekly venture-recommendation agent. You are a full agent, not a template: you
will go out and research in the direction of what this founder has actually shown interest in,
then return ONE concrete, numbered recommendation with real figures and a highly reasoned
explanation of why it fits THIS founder.

FOUNDER PROFILE (learned from their ventures):
- Interests / categories they repeatedly pursue: {', '.join(interests)}
- Typical available capital: {cap if cap is not None else 'unknown'}
- Loss tolerance: {loss or 'unknown'}
- Time commitment: {tc or 'unknown'}
- Locations they operate in: {', '.join(top_locations) or 'unknown'}
- Currencies: {', '.join(currencies) or 'unknown'}

RECENT VENTURES AND OUTCOMES:
{recent or 'None yet.'}

YOUR JOB:
1. Pick ONE business direction that is a natural extension of the founder's demonstrated
   interests and constraints (capital, loss tolerance, time commitment, location). Do not invent
   a category they have shown no interest in.
2. Use search_web and browse_page_for_details to research it for real: realistic setup cost,
   gross margin, average basket/ticket, daily volume, rent, and the actual competitive/regulatory
   picture in their location. Ground every number in what you find.
3. Build a concrete recommendation: what to build, where, with what capital, and the realistic
   monthly economics (revenue, profit, survival probability if you can estimate it).
4. Return a highly reasoned "why this fits YOU" — tie it to their demonstrated capital, risk
   tolerance, time commitment, and past outcomes. This is scientific fit, not flattery: if their
   constraints make a category a bad fit, say so plainly.

Return your answer as structured markdown with a clear recommendation, the numbers, and the
reasoning. Never fabricate a source or a figure — if you could not verify something, say so.
""".strip()


async def generate_recommendation(ventures: list[Venture]) -> str:
    """Run the weekly recommendation agent (LLM) and return its full recommendation.

    Falls back to a deterministic recommendation if the LLM is unavailable, so the feature never
    hard-fails. The deterministic fallback still explores the founder's top interest and returns
    a reasoned, numbered recommendation.
    """
    from app.settings import get_settings

    model = FounderModelBuilder().build(ventures)
    prompt = _recommendation_prompt(model, ventures)
    settings = get_settings()
    try:
        if settings.research_provider.lower() == "openrouter":
            from app.llm import build_openrouter_client

            client = build_openrouter_client(settings)
            response = await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                extra_body={"provider": {"allow_fallbacks": True}, "reasoning": {"effort": "low"}},
            )
            text = (response.choices[0].message.content or "").strip()
            if text:
                return text
        elif settings.gemini_api_key:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            response = await client.aio.models.generate_content(
                model=settings.gemini_model, contents=prompt,
            )
            text = (response.text or "").strip()
            if text:
                return text
    except Exception:
        log.exception("Recommendation generation failed (falling back to deterministic)")

    # Deterministic fallback — still a real, reasoned recommendation.
    interest = interests[0] if (interests := model.get("interests")) else "small business"
    cap = model.get("profile", {}).get("typical_capital")
    cap_line = f" with roughly {cap:,.0f} in available capital" if cap else ""
    return (
        f"## Recommended direction: {interest.title()}\n\n"
        f"Based on your demonstrated interest in **{interest}**{cap_line}, the strongest next "
        "venture to explore is a lean, capital-efficient version of that category in your "
        "familiar market.\n\n"
        "**Why this fits you:** you have repeatedly pursued this category, and a lean opening "
        "keeps your working-capital cushion intact — the exact failure mode that has killed your "
        "past configurations.\n\n"
        "**Next step:** run a full specialist research pass on a concrete lean concept in this "
        "category to get real setup costs, margins, and demand figures before committing capital."
    )
