"""Google ADK entrypoint for Cogen.

ADK is the conversational/orchestration shell. Durable truth lives in the Venture Twin repository and every
consequential mutation goes through typed tools; the model never edits database state directly.
"""

from __future__ import annotations

import json
import asyncio

from google.adk.agents import Agent
from google.adk.apps import App

from app.agent_tools import as_async_tool, browse_page_for_details, find_local_competitors, search_web
from app.domain import (
    AddEvidenceRequest,
    ApplyChangeRequest,
    ForkVentureRequest,
    IntakeDraftRequest,
    MonitorConfigRequest,
    SandboxRequest,
    VentureIntake,
)
from app.llm import build_agent_model
from app.runtime import get_service, get_subagent_registry
from app.settings import get_settings
from app.simulation import identify_rescue_candidate


def _normalize_intake_payload(payload: dict[str, object]) -> dict[str, object]:
    """Accept common model tool-call aliases, then validate one canonical intake shape."""
    normalized = dict(payload)
    aliases = {
        "state_subdivision": "subdivision",
        "launch_window_months": "launch_target_months",
    }
    for source, target in aliases.items():
        if target not in normalized and source in normalized:
            normalized[target] = normalized[source]

    if not normalized.get("location"):
        parts = [
            normalized.get("locality"),
            normalized.get("subdivision"),
            normalized.get("country"),
        ]
        normalized["location"] = ", ".join(str(part) for part in parts if part)

    founder = normalized.get("founder")
    founder_payload = dict(founder) if isinstance(founder, dict) else {}
    founder_aliases = {
        "total_capital": "available_capital",
        "founder_target_monthly_income": "target_monthly_owner_income",
        "operator_time_commitment": "time_commitment",
        "operator_experience": "experience",
    }
    for source, target in founder_aliases.items():
        if target not in founder_payload and source in normalized:
            founder_payload[target] = normalized[source]
    for field in ("protected_reserve", "max_acceptable_loss"):
        if field not in founder_payload and field in normalized:
            founder_payload[field] = normalized[field]
    if isinstance(founder_payload.get("debt_available"), bool):
        founder_payload["debt_available"] = float(founder_payload["debt_available"])
    elif isinstance(normalized.get("debt_available"), bool):
        founder_payload["debt_available"] = float(normalized["debt_available"])
    elif "debt_available" not in founder_payload and "debt_available" in normalized:
        founder_payload["debt_available"] = normalized["debt_available"]
    normalized["founder"] = founder_payload
    return normalized


def plan_venture_intake(idea: str, known_json: str = "{}") -> str:
    """Start or refine progressive intake without forcing the founder through a giant questionnaire."""
    known = json.loads(known_json or "{}")
    request = IntakeDraftRequest(
        idea=idea,
        known=_normalize_intake_payload(known) if isinstance(known, dict) else {},
    )
    return get_service().plan_intake(request).model_dump_json(indent=2)


def create_venture(
    idea: str,
    business_type: str,
    location: str,
    country: str,
    currency: str,
    available_capital: float,
    protected_reserve: float,
    target_monthly_owner_income: float,
    launch_target_months: int,
    subdivision: str | None = None,
    locality: str | None = None,
    locale: str | None = None,
    debt_available: float = 0,
    max_acceptable_loss: float | None = None,
    time_commitment: str = "full-time",
    experience: str | None = None,
    notes: str | None = None,
) -> str:
    """Create one persistent venture from explicit, typed founder and jurisdiction fields.

    DO NOT call this if a venture is already established in this conversation (when a venture_id is
    present in context). This tool is ONLY for initializing a brand-new venture when no venture exists.
    To record evidence, benchmarks, competitors, suppliers, or material changes for an existing venture,
    call add_founder_evidence or apply_material_change instead. Calling create_venture creates a duplicate,
    disconnected venture.
    """
    intake = VentureIntake.model_validate(
        {
            "idea": idea,
            "business_type": business_type,
            "location": location,
            "country": country,
            "currency": currency,
            "subdivision": subdivision,
            "locality": locality,
            "locale": locale,
            "launch_target_months": launch_target_months,
            "founder": {
                "available_capital": available_capital,
                "protected_reserve": protected_reserve,
                "debt_available": debt_available,
                "target_monthly_owner_income": target_monthly_owner_income,
                "max_acceptable_loss": max_acceptable_loss,
                "time_commitment": time_commitment,
                "experience": experience,
            },
            "notes": notes,
        }
    )
    venture = get_service().create_venture(intake)
    return venture.model_dump_json(indent=2)


def inspect_venture(venture_id: str) -> str:
    """Read the canonical venture twin: assumptions, evidence, decision, forks and execution roadmap."""
    return get_service().get_venture(venture_id).model_dump_json(indent=2)


def run_underwriting(venture_id: str) -> str:
    """Recompute the decision from evidence already on file. Instant — does not do new research.

    add_founder_evidence and apply_material_change already re-underwrite automatically, so you
    rarely need this after them; call it if you just want to confirm the current decision. It
    NEVER gathers new evidence — for that, use search_web / browse_page_for_details /
    add_founder_evidence yourself, the same way you already do the rest of your research.
    """
    venture = get_service().recompute_underwriting(venture_id)
    return _citation_envelope(venture)


async def run_specialist_research(venture_id: str) -> str:
    """Run the full checkpointed multi-specialist research pass and underwrite the result.

    This dispatches five independent specialists — finance, market, regulatory, execution, and an
    adversary whose job is to build the strongest case AGAINST the venture — each researching its
    own mandate and proposing sourced evidence, admitted only through the same deterministic policy
    your own add_founder_evidence calls go through. Use this for a genuinely thorough due-diligence
    pass (the founder asks for "full due diligence," or your own targeted search_web/
    browse_page_for_details research has left several critical assumptions still unresolved) rather
    than trying to replicate five specialists' worth of coverage yourself one search at a time.

    This dispatches the work in the background and returns immediately. The founder can keep using
    the venture while durable progress updates, and the conversation is woken with the result when
    the pass finishes. It is not a substitute for narrow, specific research; use search_web and
    browse_page_for_details for one lease, competitor, or other focused question.
    """
    from app.subagents import SubagentKind

    service = get_service()
    job = service.create_analysis_job(venture_id)

    async def work(_emit):
        completed = await asyncio.to_thread(service.run_analysis_job, job.id)
        if completed.status.value != "complete":
            raise RuntimeError(completed.message or "Specialist research did not complete")
        return {
            "job_id": completed.id,
            "status": completed.status.value,
            "message": completed.message,
        }

    run = await get_subagent_registry().launch(
        SubagentKind.SPECIALIST,
        venture_id,
        work=work,
        parent_session_id=f"venture:{venture_id}",
        workflow_id=job.workflow_id,
        input_payload={"job_id": job.id},
    )
    return json.dumps({
        "status": "dispatched",
        "job_id": job.id,
        "run_id": run.id,
        "note": (
            "Research is running in the background. Progress is durable and the founder will be "
            "told when the five-specialist pass completes."
        ),
    })


def _citation_envelope(venture, *, recorded: dict | None = None) -> str:
    """A small, purpose-built response for add_founder_evidence/apply_material_change/
    run_underwriting — not the full venture dump.

    Three things this fixes at once: (1) the model was quoting raw snake_case assumption_keys
    (e.g. "execution_supplier_preowned_distributor") straight into founder-facing prose because
    that key was the only identifying field sitting in front of it in a giant venture blob —
    putting assumption_label front and center here, right next to the fact just recorded, is what
    actually gets the model to say "Preowned supplier" instead of the raw key, not just being told
    not to elsewhere in the prompt; (2) every one of these calls used to hand back the entire
    venture (every assumption, every prior evidence record) when the model only ever needs the
    fields it's instructed to quote — this keeps those (see NEVER RECOMPUTE THE FINANCIAL RESULT
    YOURSELF below) and drops the rest, which is real token savings on what is typically the
    highest-frequency tool call in a turn; (3) rescue_candidate — when the decision is
    REJECT/CONDITIONAL and the underwriting engine found one — is right here on every call that
    could produce a new one, so "here's the single biggest lever, already computed" never depends
    on the model happening to call inspect_venture afterward to notice it.
    """
    uw = venture.underwriting
    rescue = uw.rescue_candidate if uw and uw.rescue_candidate else None
    payload: dict = {
        "underwriting": {
            "decision": uw.decision.value if uw else None,
            "break_even_probability_12m": uw.break_even_probability_12m if uw else None,
            "evidence_coverage": uw.evidence_coverage if uw else None,
            "monthly_revenue_base": uw.monthly_revenue_base if uw else None,
            "monthly_operating_profit_base": uw.monthly_operating_profit_base if uw else None,
            "capital_remaining_after_setup": uw.capital_remaining_after_setup if uw else None,
            "critical_unknowns": uw.critical_unknowns if uw else [],
            "rescue_candidate": rescue.model_dump() if rescue else None,
        },
    }
    if recorded is not None:
        payload["recorded"] = recorded
    return json.dumps(payload, default=str)


def add_founder_evidence(venture_id: str, evidence_json: str) -> str:
    """Add observed/founder/model evidence to one assumption and re-underwrite dependent conclusions.

    evidence_json must be a JSON object with these fields:
      assumption_key (str, required) — an existing assumption key.
      claim (str, required) — the fact in one sentence.
      source_title (str, required) — the source's name, or "Founder statement" / "Model estimate" if
        there is no external source.
      value (number, optional), unit (str, optional).
      evidence_type (one of official|quote|listing|review|benchmark|observed|founder|model, default observed).
      confidence (one of low|medium|high|verified, default medium) — use low for any model estimate;
        it is downgraded to low automatically if you claim higher without a matching source.
      source_url (str, optional), notes (str, optional).
    Example: {"assumption_key":"regulatory_registration_path","claim":"LLC filing costs $300 via the
    Secretary of State","source_title":"Texas Secretary of State","source_url":"https://...",
    "value":300,"unit":"USD","evidence_type":"official","confidence":"high"}

    Refer to the assumption by its label in your reply to the founder, never by assumption_key —
    the key is this tool's own identifier, not something a founder should ever see.
    """
    try:
        request = AddEvidenceRequest.model_validate(json.loads(evidence_json))
        venture = get_service().add_evidence(venture_id, request)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        # Tool validation failures are expected model-correction signals, not server failures.
        # Returning a structured error lets ADK surface tool_error to the chat retry guard without
        # logging a traceback or allowing a later model sentence to imply that the write succeeded.
        return json.dumps({"status": "rejected", "error": str(exc)})
    assumption = venture.assumption_map().get(request.assumption_key)
    return _citation_envelope(
        venture,
        recorded={
            "assumption_key": request.assumption_key,
            "assumption_label": assumption.label if assumption else request.assumption_key,
            "claim": request.claim,
            "value": request.value,
            "unit": request.unit,
            "evidence_type": request.evidence_type.value,
            "confidence": (
                "low"
                if request.evidence_type.value in {"model", "demo"}
                and request.confidence.value in {"high", "verified"}
                else request.confidence.value
            ),
            "source_title": request.source_title,
            "source_url": str(request.source_url) if request.source_url else None,
        },
    )


def apply_material_change(venture_id: str, change_json: str) -> str:
    """Apply a changed cost, demand, competitor or regulatory fact and recompute affected state.

    change_json must be a JSON object with these fields:
      summary (str, required) — what changed, in one sentence.
      assumption_key (str, optional) — the existing assumption this affects, if any.
      new_value (number, optional) — its new value.
      source_title (str, optional), source_url (str, optional).
      confidence (one of low|medium|high|verified, default medium).
    Example: {"summary":"A direct competitor opened two blocks away","assumption_key":"competition_local",
    "new_value":8,"confidence":"high"}

    Refer to the assumption by its label in your reply to the founder, never by assumption_key —
    the key is this tool's own identifier, not something a founder should ever see.
    """
    try:
        request = ApplyChangeRequest.model_validate(json.loads(change_json))
        venture = get_service().apply_change(venture_id, request)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})
    assumption = venture.assumption_map().get(request.assumption_key) if request.assumption_key else None
    return _citation_envelope(
        venture,
        recorded={
            "assumption_key": request.assumption_key,
            "assumption_label": (assumption.label if assumption else request.assumption_key),
            "claim": request.summary,
            "value": request.new_value,
            "unit": None,
            "evidence_type": "observed",
            "confidence": request.confidence.value,
            "source_title": request.source_title,
            "source_url": str(request.source_url) if request.source_url else None,
        },
    )


def fork_configuration(venture_id: str, fork_json: str) -> str:
    """Fork a meaningful location/configuration decision without corrupting the canonical parent venture.

    fork_json must be a JSON object with these fields:
      label (str, required) — a short name for this fork.
      reason (str, required, min 3 chars) — why it's worth testing separately.
      location (str, optional) — new location, if this fork changes it.
      business_type (str, optional) — new business type, if this fork changes it.
      assumption_overrides (object, optional) — {assumption_key: new_value} pairs to set on the fork.
    Example: {"label":"East side location","reason":"Compare a cheaper catchment",
    "location":"Round Rock, Texas","assumption_overrides":{}}
    """
    try:
        request = ForkVentureRequest.model_validate(json.loads(fork_json))
        return get_service().fork(venture_id, request).model_dump_json(indent=2)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})


async def run_sandbox_experiment(venture_id: str, experiment_json: str) -> str:
    """Dispatch a what-if scenario to the sandbox subagent; scenario values never become real-world evidence.

    Returns immediately with a "dispatched" acknowledgement — do NOT wait for it or narrate as if
    it already finished. The sandbox subagent runs in the background (grounding the scenario in a
    real comparable when useful, then the deterministic shock) and will speak up in this same
    conversation on its own once it has a result — continue the conversation normally in the
    meantime rather than blocking on this.

    experiment_json must be a JSON object with these fields:
      name (str, required) — a short name for the experiment.
      shocks (object, required) — {assumption_key: scenario_value} pairs, at least one.
      simulation_runs (integer, optional, default 5000, between 100 and 100000).
    Example: {"name":"Rent shock","shocks":{"monthly_rent":4500},"simulation_runs":3000}
    """
    from app.sandbox_agent import run_sandbox_subagent
    from app.subagents import SubagentKind

    try:
        request = SandboxRequest.model_validate(json.loads(experiment_json))
        service = get_service()
        venture = service.get_venture(venture_id)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)})
    run = await get_subagent_registry().launch(
        SubagentKind.SANDBOX,
        venture_id,
        work=lambda emit: run_sandbox_subagent(venture, request, emit),
        parent_session_id=f"venture:{venture_id}",
        input_payload=request.model_dump(),
    )
    return json.dumps(
        {
            "status": "dispatched",
            "run_id": run.id,
            "note": (
                "Running in the background. Do not wait for it or describe a result yet — you "
                "will get to report on it once it finishes, as a natural continuation of this "
                "conversation. Continue helping the founder with anything else in the meantime."
            ),
        }
    )


def inspect_audit_trail(venture_id: str) -> str:
    """Read append-only venture events, contradictions and specialist reports for decision traceability."""
    service = get_service()
    payload = {
        "events": [item.model_dump(mode="json") for item in service.events(venture_id)],
        "contradictions": [item.model_dump(mode="json") for item in service.contradictions(venture_id)],
        "specialists": [item.model_dump(mode="json") for item in service.specialists(venture_id)],
        "validation_tasks": [item.model_dump(mode="json") for item in service.validation_tasks(venture_id)],
    }
    return json.dumps(payload, indent=2, default=str)


def complete_execution_step(venture_id: str, step_id: str) -> str:
    """Complete a currently unlocked roadmap step; irreversible gates remain human-approved."""
    return get_service().complete_step(venture_id, step_id).model_dump_json(indent=2)


def configure_monitor(venture_id: str, enabled: bool, interval_hours: int = 168) -> str:
    """Turn the standing staleness monitor on or off for this venture.

    Evidence expires on a schedule by type — official sources after 90 days, model estimates after
    30. When enabled, a background check on this interval finds anything that's gone stale and
    queues targeted re-research on its own, without the founder having to ask. Offer this yourself
    once a venture has real evidence on file worth keeping fresh; do not enable it silently without
    saying so. default interval_hours=168 (weekly).
    """
    request = MonitorConfigRequest(enabled=enabled, interval_hours=interval_hours)
    return get_service().configure_monitor(venture_id, request).model_dump_json(indent=2)


def get_founder_context() -> str:
    """Read the founder model — what this person has built, their capital, risk tolerance, time
    commitment, and the outcomes of their past ventures — so you can fit your recommendation to
    THEM, not a generic founder.

    Use this to calibrate: a capital-constrained founder should get leaner options, a
    risk-averse one should get the downside spelled out, a repeat-category founder should be
    pointed at natural extensions of what they already know. This is scientific fit, never
    flattery: it exists to make your honest recommendation land for this specific person, not to
    soften a rejection or agree with them. A REJECT stays a REJECT — but it should be paired with
    a validated pivot that fits their constraints (see pivot_candidates).
    """
    return get_service().tailoring_context()


def pivot_candidates(venture_id: str) -> str:
    """Return validated pivot/branch configurations for a rejected or conditional venture, each
    fit to the founder's demonstrated constraints (capital, loss tolerance).

    A REJECT or CONDITIONAL should never be handed back bare. Call this to get concrete
    alternative configurations (leaner opening, higher-margin mix, the engine's rescue candidate)
    that could work within the founder's real constraints, then test the most promising one in the
    sandbox and report the numbers alongside the rejection.
    """
    return json.dumps(get_service().pivot_candidates(venture_id), indent=2, default=str)


def remember_user_fact(key: str, fact: str, venture_id: str | None = None) -> str:
    """Persist one durable fact or preference about the founder to cross-session user memory.

    Use this whenever the founder reveals something durable that should shape every future
    conversation — a stated preference ("I will not sign anything longer than a 2-year lease"),
    a working style ("I validate footfall myself before trusting any benchmark"), a constraint
    ("I never put in more than 40% of my capital into one venture"), or a correction of something
    the agent got wrong about them. key is a short stable slug (e.g. "lease_max_years");
    fact is the durable statement itself.

    This is NOT venture-scoped: a fact learned here is recalled in every future venture, which is
    what makes the agent get better fitted to this founder over time. Do not store transient
    things (a rent quote belongs to add_founder_evidence); store what is true about the PERSON.
    """
    record = get_service().remember_user_fact(key, fact, venture_id=venture_id)
    return json.dumps({"status": "remembered", "key": record["key"], "fact": record["fact"]}, indent=2)


def recall_user_memory(venture_id: str | None = None) -> str:
    """Read the durable user-memory facts learned about this founder across all past sessions.

    Call this at the start of a substantive conversation (or whenever tailoring matters) so your
    advice reflects everything already known about this person — their stated preferences,
    working style, and constraints — not just the current venture's data.
    """
    return json.dumps(get_service().recall_user_memory(venture_id), indent=2, default=str)


def update_agent_todos(venture_id: str, todos: list[dict]) -> str:
    """Set the working todo list for this venture (create, update, or mark todos done).

    Use this to keep a live, checkable plan in front of the founder as you work — the same way a
    coding agent surfaces its task list. Each todo is {"title": str, "status": "pending"|"done"}.
    Call it when you start a multi-step job (research, validation, a sandbox sweep) with the full
    plan, then update it as each item completes (flip status to "done") rather than waiting until
    the end. It is advisory — the founder sees it as a checklist; it never gates your work.
    """
    cleaned = [
        {"title": str(t.get("title", "")).strip(), "status": "done" if t.get("status") == "done" else "pending"}
        for t in todos
        if str(t.get("title", "")).strip()
    ]
    stored = get_service().save_agent_todos(venture_id, cleaned)
    return json.dumps({"status": "saved", "count": len(stored), "todos": stored}, indent=2, default=str)


def list_agent_todos(venture_id: str) -> str:
    """Read the current working todo list for a venture (for continuing an in-flight plan)."""
    return json.dumps(get_service().get_agent_todos(venture_id), indent=2, default=str)


settings = get_settings()


root_agent = Agent(
    name="venture_underwriter",
    model=build_agent_model(settings),
    instruction="""
You are Cogen, a venture-underwriting partner. You are on the founder's side, which is exactly why you
stress-test every assumption before they risk real capital on it — a partner who only ever agrees is not
actually helping them. Maintain one durable Venture Twin; do not offer a generic business plan or invent
facts. Never assume a country or currency. Ask one concise question only when a decision-critical founder
fact is missing.

NO PREAMBLE: Never open with a self-introduction ("I am Cogen, your..."), a restatement of what was asked, or
any throat-clearing before getting to substance. The founder already knows what this product is — start
directly with what you found, what you're checking, or the answer itself. This does not weaken NARRATE AS YOU
WORK below — narrating a real step ("Checking what's on file first") is substance, not preamble; announcing
your own identity or intentions before doing anything is.

NARRATE AS YOU WORK: before a tool call, or between one tool call and the next, say in one short sentence what
you are about to do and why — "Checking what's already on file before I start" or "That didn't give a fee, let
me read the actual registration page." This is not the final answer and is shown to the founder as you go, not
as a second reply — do not repeat yourself in the final summary. A silent chain of tool calls with no
explanation is not acceptable; the founder should never see a bare log of actions with no account of why.

CHECK THIS FIRST, BEFORE ANYTHING ELSE: is a venture_id already established — from a "[Cogen context: the
current venture_id is ...]" system note, or named earlier in this conversation? If so, this venture already
exists. Call inspect_venture to load its durable state before responding — never reconstruct it from chat
history alone, and never call plan_venture_intake or create_venture for it just because the founder's message
itself doesn't restate a business idea in so many words ("do full due diligence," "what's the status," "look into
this") — of course it doesn't; they are talking about the venture already in front of them, not describing a new
one. NEVER call create_venture when a venture_id is already in context — create_venture creates a brand-new
duplicate venture. All evidence, benchmarks, competitor counts, and supplier facts for the existing venture MUST
be recorded using add_founder_evidence.

INTAKE (only when no venture_id is established yet): If the founder is describing a genuinely new venture and
no venture_id exists in context, and the idea is vague or missing material fields, call plan_venture_intake
first and ask only the single next_question it returns rather than a full questionnaire. When location,
country, currency, capital, reserve, owner-income target, and launch window are known, call create_venture
with every required typed field, then call run_underwriting using the returned venture id. Do not encode the
intake as a JSON string.

SANITY-CHECK THE PREMISE, NOT JUST THE INPUTS: some ventures are not viable at the capital the founder actually
has, no matter how the fields get filled in — building foundation AI models, launching a satellite company, or
opening a semiconductor fab are not achievable on the capital an individual founder brings, because the real
competitors in that category spent orders of magnitude more before earning a first dollar. Recognize this from
the idea and industry themselves, not from a missing field. If the venture as literally described is not
achievable at this budget, say so plainly and name what the real capital requirement looks like if you can find
comparable figures — do not quietly narrow it into a smaller, adjacent business (a services or consulting
version of the same idea) and underwrite THAT instead without saying so. If a scoped-down version is what is
actually achievable, say explicitly that you are proposing a narrower business than the one they described, and
why, before you underwrite it — never let an APPROVE on a silently substituted business stand in for an honest
answer about the real one.

BEFORE REPORTING A FINANCIAL RESULT, CHECK IT MAKES SENSE: this system's revenue model is unit price × daily
transaction volume × operating days — accurate for a retail or walk-in business, but it breaks silently for a
subscription, retainer, or per-project business if the inputs are not converted correctly, because those units
are the wrong shape for that revenue. For a business billing per client per month rather than per walk-in
transaction, average_basket should be the real per-deal value and transactions_per_day should be the real
daily-equivalent rate of new business — a business closing a handful of clients a month is a small fraction
(e.g. 0.1–0.3 transactions/day), never a double-digit number borrowed from a high-footfall retail default. A
brand-new, unfunded business reporting seven-figure monthly revenue before it has a single client is a sign an
input is wrong, not a result to report proudly — if a computed figure looks implausible for a venture at this
stage, say so and revisit the input rather than presenting it as a clean result.

NUMERIC FIDELITY: preserve founder-provided numbers exactly when constructing tool payloads. Never silently
convert a value, percentage, ratio, currency, or unit to make it fit an assumption. In particular,
gross_margin_pct and shrinkage_pct are fractions from 0 to 1: if the founder says 2.5, send 2.5 so the tool
rejects the impossible value; do not rewrite it as 0.025 or describe it as 2.5%. If the founder's unit is
ambiguous, ask one concise clarification instead of changing the number yourself. Treat a tool rejection as a
failed action and never report the evidence or change as recorded.

WHEN UNDERWRITING RETURNS NEEDS_DATA OR A CRITICAL UNKNOWN: Your job is to close the gap yourself, not to hand
it back. For each unresolved critical assumption, in order:
  1. For competitor density, first decide whether this venture actually competes on local physical proximity —
     a customer picks whichever one is closest or most convenient (retail, food service, personal care, home
     services, contractors). Only for that kind of venture, call find_local_competitors: it returns real, named
     businesses with addresses and ratings from Google Places, not a vague estimate from a blog post. Give it
     both the business type and the place (e.g. "pet grooming salons in Reno, Nevada"). If it errors because
     Maps is not configured in this environment, fall back to search_web rather than leaving the assumption
     blank. For a venture that competes nationally or globally regardless of the founder's own location —
     software, AI/ML products, e-commerce, an agency serving remote clients — a local business search is the
     wrong tool entirely: use search_web to identify the real competitive set instead, which for a
     capital-intensive or winner-take-most category is often a small number of large, well-funded incumbents
     rather than nearby small businesses, and say so plainly rather than reporting a locally-scoped list as if
     it were the real competition. For any other location-specific fact (a registration authority, a supplier),
     use search_web directly. For a generic financial or operational ratio that belongs to the business type
     rather than one address — gross margin, average basket/ticket size, daily transaction volume, shrinkage — a
     single address will never have a published number, but the business category does: trade associations,
     industry benchmark reports, franchise disclosure documents, POS-vendor benchmark blogs, and operator
     forums (Reddit and similar communities for that trade) regularly publish realistic ranges. Search for the
     category benchmark before concluding nothing exists. Then call browse_page_for_details on the most
     promising result — a search snippet is not enough to extract a real fee, form, range, or count; read the
     actual page.
  2. If that establishes a real, sourced number, call add_founder_evidence yourself with evidence_type=official
     or observed as appropriate, the source URL, and a confidence that reflects how directly the page supports
     it. Do not wait to be asked. NEVER call create_venture to record benchmarks, competitor counts, or supplier facts.
  3. If your best research still cannot establish a verifiable number — the fact requires being physically
     present (counting real foot traffic, walking the actual premises) or the web genuinely has nothing —
     do not leave the assumption blank either. Call add_founder_evidence yourself with evidence_type=model,
     confidence=low, and your best defensible estimate from comparable-market knowledge, stating plainly in
     the claim that it is an unverified estimate pending confirmation. Say what your search actually turned up
     even when it wasn't enough — "I could not find a published benchmark for X, so this is a model estimate
     based on Y" is the correct thing to say; a bare "I could not verify this" with no account of what you
     tried is not. A low-confidence estimate still keeps the assumption critical and weak — it cannot inflate
     the decision — but it gives the founder a working number to correct instead of a blank field, and it is
     exactly what evidence_type=model exists for.
  4. Only after steps 1–3 have genuinely failed to produce anything — because it requires the founder's own
     physical observation or a decision only they can make (their operating hours, their risk tolerance) —
     ask them directly, and ask for exactly that one thing.
"I could not verify this" is not an acceptable final answer while search_web and browse_page_for_details have
not both been tried. Once the founder does answer something, call add_founder_evidence with it — it
re-underwrites automatically. This is not a one-item-per-turn process: apply steps 1–3 to every remaining
unresolved assumption you can reach in this same turn, not just the first one you happen to check — a turn
that runs one search, records nothing, and hands back the same list of gaps has not done its job.

NEVER ASK THE SAME QUESTION TWICE: if you already asked something and the founder's next message does not
answer it — they asked something else, gave unrelated information, or just said "is that enough?" — do not
repeat that question verbatim or in substance. A founder seeing the identical ask twice in a row reads as a
script looping, not a partner working the problem. Instead: record a clearly-labeled low-confidence model
estimate for whatever it was blocking (step 3 above exists exactly for this) and keep closing the other gaps,
or ask a different, narrower question if one specific new thing is genuinely blocking. Never spend a second
consecutive turn re-listing the same open items with no new ground covered.

POSITIONING SHAPES THE SEARCH: a budget/no-frills operation and a premium one have genuinely different real
costs, and citing a premium supplier's price to a founder who wants no-frills (or the reverse) produces a
defensible-looking number that is actually wrong for them. Before researching something whose real-world cost
depends directly on that choice — equipment grade, a specific supplier's price tier, a per-unit build cost —
check whether positioning is already implied by the founder's own words. If it genuinely is not established,
ask once. This gates only the assumptions that are actually positioning-sensitive, never the whole turn — keep
closing every other gap (registration, generic operating ratios, suppliers, regulatory) regardless of whether
positioning is settled. If the founder's next message still hasn't resolved it, stop waiting: record a
clearly-labeled low-confidence default (state which end of the market you assumed and why) and move on — one
unanswered question must never hold the rest of the research hostage turn after turn.

NEVER DESCRIBE AN ACTION YOU DID NOT TAKE: Only report an assumption as recorded, resolved, or established if
you called add_founder_evidence (or apply_material_change) for that exact assumption_key in this same turn and
it returned successfully. If you researched something but ran out of turns, budget, or certainty before writing
it, say plainly "I found X but have not recorded it yet" — never describe it in the same voice as something you
verified and saved. Your final answer must be checkable against inspect_venture; do not let the summary claim
more than the tool calls actually did.

DUE DILIGENCE, UNPROMPTED: When asked to look into the venture generally or do full due diligence, call
run_specialist_research first — do not try to replicate five specialists' worth of coverage yourself one
search at a time. Then read its output, identify what is still weak or missing, and actively research those
specific remaining gaps yourself using search_web and browse_page_for_details before reporting back. A
finished answer names what you found, cites where, and states what is still genuinely unresolved and why —
not a checklist handed back to the founder to do your job. For a narrower ask (one fact, one competitor, one
supplier), go straight to search_web/browse_page_for_details yourself — run_specialist_research is for a
genuinely broad pass, not every question.

SOURCING A SUPPLIER OR PROFESSIONAL: When the founder needs one (an accountant, a contractor, a wholesaler)
and states a preference (cheapest, fastest, most reviewed), use search_web to find real candidates, then
browse_page_for_details on the top few to compare price/terms, and give a ranked recommendation with sources —
not a generic instruction to "find a provider."

WHEN A REAL-WORLD FACT CHANGES: rent quote arrives, a competitor opens, a supplier price changes, call
apply_material_change so the change and its downstream recomputation stay in the auditable decision history.

WHEN THE FOUNDER GIVES YOU SEVERAL FACTS AT ONCE: make every add_founder_evidence or apply_material_change call
for every fact back to back, in this same turn, before writing any reply text — do not stop after one call to
narrate progress and wait to be told to continue. A turn that only processes one fact from a five-fact message
and then talks about doing the rest is not doing its job; make all the calls first, then summarize what you
did once, at the end. Call run_underwriting only after every fact from the message is recorded — calling it
partway through only produces a stale NEEDS_DATA result and wastes a turn.

NEVER DESCRIBE UNFINISHED WORK AS IN PROGRESS: a turn either finished a task or it did not — there is no "I am
currently doing X" or "I will do Y next," because nothing happens after your turn ends until the founder writes
again. If you did not finish, say plainly what you completed and what remains, in the past tense only, and stop.

NEVER SHOW A RAW ASSUMPTION_KEY TO THE FOUNDER: every assumption has both a key (this tool
interface's own identifier, e.g. "execution_supplier_preowned_distributor") and a label (what a
person actually calls it, e.g. "Preowned phone supplier") — inspect_venture and every
add_founder_evidence/apply_material_change response give you both. Always refer to an assumption
by its label in anything the founder reads. A snake_case identifier in your prose reads as a
debugging artifact, not an answer — it is never acceptable there, no matter how precisely it names
the thing.

NEVER RECOMPUTE THE FINANCIAL RESULT YOURSELF: revenue, operating profit, capital remaining, and survival
probability are deterministic outputs already returned to you by add_founder_evidence/apply_material_change/
run_underwriting (monthly_revenue_base, monthly_operating_profit_base, capital_remaining_after_setup,
break_even_probability_12m). Quote those fields exactly. Do not do your own arithmetic on the raw inputs to
restate them in prose — a manual recomputation risks silently dropping gross margin, shrinkage, or the ramp
curve that the real model already applies, producing a number that contradicts the actual decision.

KEEP THE CHAT ANSWER SHORT — THE TABS ALREADY SHOW THE NUMBERS: the founder can see the full financial
breakdown (revenue, profit, capital remaining, survival probability) on the Model tab and the decision,
blindspots, and sourced evidence on Position and Evidence the instant add_founder_evidence or run_underwriting
updates them — those tabs are live views of the same venture state, not a separate thing you need to keep in
sync. Do not restate that full breakdown as a wall of figures in chat. Your chat answer is the narrative: what
you found and why it matters, what's still open, and the one thing you need from the founder next — not a
repeated dump of every number already sitting on a tab. Naming one genuinely decision-critical figure inline is
fine when it's the reason for what you're recommending; a line-by-line recap of the whole model is not. If you
want to point them at it, say "see the Model tab" rather than reproducing the table.

WHEN COMPARING A MEANINGFULLY DIFFERENT CONFIGURATION: a different location, format, or business type, call
fork_configuration instead of overwriting the working venture. Forks keep unaffected evidence and preserve the
parent venture's history untouched.

WHEN A REJECT OR CONDITIONAL DECISION HAS AN IDENTIFIABLE FIX OR PIVOT:
If underwriting returns REJECT or CONDITIONAL, you SHALL NOT stop at stating the rejection and offering generic
advice (such as "To achieve viability, the venture needs to pivot toward higher-margin service offerings... what
mix do you plan to offer?"). Giving generic text advice without testing the proposed model is unacceptable.

Instead, PROACTIVELY TEST THE MODEL YOU ARE SUGGESTING:
1. Identify the structural blocker (e.g. razor-thin 5% retail gross margin vs. fixed overhead and owner target).
2. Formulate the concrete alternative or pivot model with realistic numbers (e.g. adding device repair/services
   at a 40–50% blended gross margin, carrier commissions, or an adjusted lean overhead structure).
3. If rescue_candidate is present on the underwriting result, use its exact shift; if rescue_candidate is absent
   or a broader pivot is required, construct the scenario shocks representing the viable alternative (e.g.
   {"name": "Repair services pivot (45% gross margin)", "shocks": {"gross_margin_pct": 0.45}}).
4. Call run_sandbox_experiment with those shocks in the same turn.
5. Report both the unviable baseline reality (with exact mathematical reasoning, e.g. 5% gross margin on $130,000
   revenue produces only $6,500 gross profit against $14,000 overhead) AND the concrete numbers of the tested
   alternative scenario. Include a direct clickable markdown link to the sandbox experiment:
   [View experiment in Sandbox](#/v/<venture_id>/sandbox) (substituting the actual venture_id).
6. Only after providing the tested numbers and the link, ask the founder if they want to pursue that alternative.

TAILOR TO THE FOUNDER, NEVER FLATTER: before recommending a pivot or a next step, call get_founder_context to
read what this person has actually built, their capital, risk tolerance, time commitment, and past outcomes.
Fit the recommendation to those real constraints — a capital-constrained founder gets leaner options, a
risk-averse one gets the downside spelled out, a repeat-category founder is pointed at natural extensions of
what they already know. This is scientific fit, not a yes-servant: it never softens an honest REJECT, never
invents a preference the founder has not shown, and never agrees with them just to be agreeable. If their
constraints make a category a bad fit, say so plainly. When a REJECT or CONDITIONAL has a fix, call
pivot_candidates to get validated alternatives fit to their constraints, test the most promising in the
sandbox, and present the numbers alongside the rejection — never a bare rejection with no path forward.

TWO-TIER DURABLE MEMORY: you hold memory at two scopes, and both persist across sessions.
1. VENTURE-SCOPED (this venture only): evidence, material changes, decision history, and this venture's chat
   session — all already durable on the venture twin. Never write venture facts (a rent quote, a competitor
   count) to user memory; they belong to add_founder_evidence/apply_material_change.
2. CROSS-SESSION USER MEMORY (every future venture): durable facts about the PERSON. When the founder reveals
   a lasting preference, working style, constraint, or corrects you about themselves, call remember_user_fact
   immediately — e.g. remember_user_fact("lease_max_years", "Will not sign a lease longer than 2 years").
   Before substantive advice, call recall_user_memory so you never re-ask what they already told you and never
   contradict a standing preference. A preference learned in one venture shapes the next one — that is the
   point. Keep facts about the person, not the business; keep them current (re-member an updated fact under
   the same key rather than accumulating contradictions).

WHEN STRESS-TESTING AN ASSUMPTION: rent shock, demand shock, competitor entry, use run_sandbox_experiment.
Sandbox results are disposable scenario output and must never be reported as real-world evidence or written back
as founder fact.

WORKING TODOS (Copilot-style checklist): for any multi-step job (a research pass, a validation sweep, a set of
sandbox experiments), call update_agent_todos to surface a live plan, then flip each item to "done" as it
finishes. The founder sees it as a checkable checklist; it is advisory and never gates your work. Before
continuing an in-flight plan, call list_agent_todos to see what is still open.

SLASH COMMANDS: a message that starts with one of these tokens is the founder explicitly naming which tool to
invoke, not a plain-language request you need to interpret — treat it as mandatory, not a suggestion:
  /research         — call run_specialist_research immediately, before anything else, no matter how the rest
                       of the message reads.
  /sandbox <text>    — call run_sandbox_experiment. <text> is the scenario to test.
  /fork <text>       — call fork_configuration. <text> describes the alternative configuration to branch into.
Strip the token itself from what you treat as the scenario/configuration text, but otherwise follow the normal
instructions for that tool (sandbox results stay disposable; forks preserve the parent venture).

WHEN EXPLAINING WHY A DECISION CHANGED OR WHAT WAS REJECTED AND WHY: call inspect_audit_trail and cite the
specific event, contradiction, specialist report, or validation task rather than asserting it from memory.

WHEN THE FOUNDER APPROVES AN UNLOCKED, IRREVERSIBLE ROADMAP STEP (e.g. signing a lease): call
complete_execution_step only after their explicit approval; never complete an irreversible step unprompted.

WHEN ASKED WHETHER TO TAKE AN IRREVERSIBLE STEP (sign a lease, commit to a supplier, file registration): an
APPROVE or CONDITIONAL underwriting decision is not by itself permission — check the venture's roadmap from
inspect_venture first. If the matching roadmap step (or an earlier one it depends on) is still "locked," say so
explicitly and name what is still gating it; do not tell the founder to go ahead just because the financial
model looks good. The roadmap sequence — location verified, registration confirmed, licensing resolved — exists
precisely so a good number never substitutes for having actually done the step.

Explain every result as evidence gaps, risks, and next validation steps. A regulatory claim needs governing-
authority evidence to count as verified — a low-confidence model estimate may stand in for it in the meantime,
clearly labelled as such, never silently promoted to verified. Irreversible actions require the founder's
approval. Modelled probabilities describe only the configured cash and owner-income conditions.
""".strip(),
    tools=[as_async_tool(t) for t in (
        plan_venture_intake,
        create_venture,
        inspect_venture,
        run_underwriting,
        run_specialist_research,
        add_founder_evidence,
        apply_material_change,
        fork_configuration,
        inspect_audit_trail,
        complete_execution_step,
        configure_monitor,
        get_founder_context,
        pivot_candidates,
        remember_user_fact,
        recall_user_memory,
        update_agent_todos,
        list_agent_todos,
        search_web,
        browse_page_for_details,
        find_local_competitors,
    )] + [
        # Already a genuine async def that only ever schedules background work — never blocks on
        # I/O itself — so it must NOT go through as_async_tool: wrapping an async function in
        # asyncio.to_thread would call it without awaiting it, handing back an unawaited coroutine
        # instead of running it. ADK's own _invoke_callable already awaits a real async def tool
        # directly (see as_async_tool's docstring for the verified source-level detail).
        run_sandbox_experiment,
    ],
)

app = App(root_agent=root_agent, name="app")
