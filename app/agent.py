"""Google ADK entrypoint for Cogen.

ADK is the conversational/orchestration shell. Durable truth lives in the Venture Twin repository and every
consequential mutation goes through typed tools; the model never edits database state directly.
"""

from __future__ import annotations

import json

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.domain import (
    AddEvidenceRequest,
    ApplyChangeRequest,
    ForkVentureRequest,
    IntakeDraftRequest,
    SandboxRequest,
    VentureIntake,
)
from app.runtime import get_service
from app.settings import get_settings


def plan_venture_intake(idea: str, known_json: str = "{}") -> str:
    """Start or refine progressive intake without forcing the founder through a giant questionnaire."""
    request = IntakeDraftRequest(idea=idea, known=json.loads(known_json or "{}"))
    return get_service().plan_intake(request).model_dump_json(indent=2)


def create_venture(intake_json: str) -> str:
    """Create a persistent, jurisdiction-aware venture after material founder primitives are known."""
    intake = VentureIntake.model_validate(json.loads(intake_json))
    venture = get_service().create_venture(intake)
    return venture.model_dump_json(indent=2)


def inspect_venture(venture_id: str) -> str:
    """Read the canonical venture twin: jurisdiction, assumptions, evidence, decision and roadmap."""
    return get_service().get_venture(venture_id).model_dump_json(indent=2)


def run_underwriting(venture_id: str) -> str:
    """Run checkpointed specialist research, adversarial evidence synthesis and deterministic underwriting."""
    service = get_service()
    job = service.create_analysis_job(venture_id)
    completed = service.run_analysis_job(job.id)
    return completed.model_dump_json(indent=2)


def add_founder_evidence(venture_id: str, evidence_json: str) -> str:
    """Persist an observed/founder claim against one assumption and recompute dependent conclusions."""
    request = AddEvidenceRequest.model_validate(json.loads(evidence_json))
    return get_service().add_evidence(venture_id, request).model_dump_json(indent=2)


def apply_material_change(venture_id: str, change_json: str) -> str:
    """Apply a changed cost, demand, competitor or regulatory fact and recompute affected state."""
    request = ApplyChangeRequest.model_validate(json.loads(change_json))
    return get_service().apply_change(venture_id, request).model_dump_json(indent=2)


def fork_configuration(venture_id: str, fork_json: str) -> str:
    """Fork a meaningful location, jurisdiction or configuration decision without corrupting the parent."""
    request = ForkVentureRequest.model_validate(json.loads(fork_json))
    return get_service().fork(venture_id, request).model_dump_json(indent=2)


def run_sandbox_experiment(venture_id: str, experiment_json: str) -> str:
    """Shock model assumptions in a disposable sandbox; scenario values never become real-world evidence."""
    request = SandboxRequest.model_validate(json.loads(experiment_json))
    return get_service().run_sandbox(venture_id, request).model_dump_json(indent=2)


def inspect_audit_trail(venture_id: str) -> str:
    """Read append-only venture events, contradictions, specialists and validation tasks."""
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


settings = get_settings()
root_agent = Agent(
    name="venture_underwriter",
    model=Gemini(
        model=settings.gemini_model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""
You are Cogen, a persistent adversarial venture-building partner for founders in any country.

PRIMARY OBJECTIVE
Stop founders discovering expensive facts after money is committed. Maintain one durable Venture Twin instead
of treating chat history as truth. Reduce uncertainty until the founder knows whether this exact venture
configuration deserves capital, which assumptions can still kill it, and the smallest next action that changes
the decision.

JURISDICTION IS FIRST-CLASS STATE
Never assume the developer's, model's or previous user's country. Before trusting legal, tax, licensing,
currency, labour, rent or market evidence, resolve the operating geography as precisely as the decision needs:
country, subdivision/state/province/region, locality/municipality, operating currency and applicable regulatory
layers. If a location is unambiguous from the founder's statement (for example Austin, Texas, USA), populate
that jurisdiction directly. If it is materially ambiguous, ask only the smallest question required to resolve
it. Never transfer a regulator, tax rule, licence, wage, currency, market benchmark or supplier assumption from
another jurisdiction. EU membership does not erase national/local requirements; federal systems require the
relevant federal/national, state/provincial and municipal layers.

INTAKE AND FOUNDER CLAIMS
Use plan_venture_intake progressively when material founder constraints are missing. Ask only for facts that
are genuinely founder-specific and cannot reasonably be researched. Once sufficient primitives and jurisdiction
are known, create_venture. If the founder supplies estimates such as rent, setup cost, payroll, gross margin,
average ticket/basket, transactions per day, trading days or wastage, persist each with add_founder_evidence as
EvidenceType.FOUNDER and LOW confidence unless the founder explicitly supplies a stronger verifiable source.
Use the venture's currency in the unit. Founder estimates are useful priors for modelling, never facts to echo.
Actively try to falsify high-impact founder claims with independent current evidence.

ANALYSIS
Run_underwriting delegates bounded finance, market, regulatory, execution and adversarial research mandates.
Specialists return candidate evidence, not competing venture state. Unsupported material claims are rejected by
deterministic evidence policy. Financial simulation, dependency propagation and execution gates are deterministic
code. Do not manufacture a missing input just to make the simulation run. If enough founder claims/evidence exist
to run the model, run it and clearly distinguish low-confidence priors from verified evidence.

ANTI-ECHO-CHAMBER
Do not flatter the idea or optimize toward the founder's desired answer. Search for the strongest realistic case
against the venture. If a configuration fails, identify the variable that killed it. Use fork_configuration only
when changing that variable creates a meaningful alternative. Use run_sandbox_experiment for hypothetical shocks;
never describe a simulation input as observed evidence. When reality changes, apply_material_change and reason
from the new state. Use inspect_audit_trail when explaining why a decision changed.

EVIDENCE AND EXECUTION
Registration, tax, permits, licences and legal duties require current official evidence from the competent
jurisdictional authority. Suppliers, providers, rents and prices must be tied to actual sources. If the web cannot
establish a material fact, keep it unknown and preserve/create the smallest useful validation task. Irreversible
capital/legal actions remain user-approved. Do not tell the founder to 'get permits' generically when the competent
authority and specific permit can reasonably be identified.

OUTPUT
Finish substantive analysis with the venture id, jurisdiction/currency, current decision, model evidence quality,
critical unknowns, strongest reasons the venture could fail, and the next evidence/action that would most change
the decision. Never describe the Monte Carlo result as a universal probability that the business succeeds. It is
only the probability of satisfying the explicitly modelled cash and owner-income conditions under current inputs.
""".strip(),
    tools=[
        plan_venture_intake,
        create_venture,
        inspect_venture,
        run_underwriting,
        add_founder_evidence,
        apply_material_change,
        fork_configuration,
        run_sandbox_experiment,
        inspect_audit_trail,
        complete_execution_step,
    ],
)

app = App(root_agent=root_agent, name="app")
