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
    """Create a persistent venture after the material founder primitives are known."""
    intake = VentureIntake.model_validate(json.loads(intake_json))
    venture = get_service().create_venture(intake)
    return venture.model_dump_json(indent=2)


def inspect_venture(venture_id: str) -> str:
    """Read the canonical venture twin: assumptions, evidence, decision, forks and execution roadmap."""
    return get_service().get_venture(venture_id).model_dump_json(indent=2)


def run_underwriting(venture_id: str) -> str:
    """Run checkpointed specialist research, adversarial evidence synthesis and deterministic underwriting."""
    service = get_service()
    job = service.create_analysis_job(venture_id)
    completed = service.run_analysis_job(job.id)
    return completed.model_dump_json(indent=2)


def add_founder_evidence(venture_id: str, evidence_json: str) -> str:
    """Add observed/founder evidence to one assumption and re-underwrite dependent conclusions."""
    request = AddEvidenceRequest.model_validate(json.loads(evidence_json))
    return get_service().add_evidence(venture_id, request).model_dump_json(indent=2)


def apply_material_change(venture_id: str, change_json: str) -> str:
    """Apply a changed cost, demand, competitor or regulatory fact and recompute affected state."""
    request = ApplyChangeRequest.model_validate(json.loads(change_json))
    return get_service().apply_change(venture_id, request).model_dump_json(indent=2)


def fork_configuration(venture_id: str, fork_json: str) -> str:
    """Fork a meaningful location/configuration decision without corrupting the canonical parent venture."""
    request = ForkVentureRequest.model_validate(json.loads(fork_json))
    return get_service().fork(venture_id, request).model_dump_json(indent=2)


def run_sandbox_experiment(venture_id: str, experiment_json: str) -> str:
    """Shock model assumptions in a disposable sandbox; scenario values never become real-world evidence."""
    request = SandboxRequest.model_validate(json.loads(experiment_json))
    return get_service().run_sandbox(venture_id, request).model_dump_json(indent=2)


def inspect_audit_trail(venture_id: str) -> str:
    """Read append-only venture events, contradictions and specialist reports for decision traceability."""
    service = get_service()
    payload = {
        "events": [item.model_dump(mode="json") for item in service.events(venture_id)],
        "contradictions": [
            item.model_dump(mode="json") for item in service.contradictions(venture_id)
        ],
        "specialists": [item.model_dump(mode="json") for item in service.specialists(venture_id)],
        "validation_tasks": [
            item.model_dump(mode="json") for item in service.validation_tasks(venture_id)
        ],
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
You are Cogen, the persistent adversarial venture-building partner.

Your objective is to stop founders learning expensive facts after money is committed. Maintain one durable
Venture Twin instead of reasoning from chat memory. Do not flatter an idea. Reduce uncertainty until the
founder can decide whether the venture deserves capital, what could still kill it, and what must happen next.

Use plan_venture_intake progressively. Ask only for founder-specific information that materially affects the
decision and cannot reasonably be researched. Once sufficient founder primitives exist, create_venture and
run_underwriting. That workflow delegates narrow finance, market, regulatory, execution and adversarial
research mandates; specialists return candidate evidence, not competing truths. Unsupported material claims
are rejected by deterministic evidence policy. Financial simulation and gate logic are deterministic code.

If a configuration fails, identify the variable that killed it. Use fork_configuration only when changing that
variable creates a meaningful alternative. Use run_sandbox_experiment for hypothetical shocks; never describe
a simulation input as observed evidence. When reality changes, apply_material_change and reason from the new
state. Use inspect_audit_trail when explaining why a decision changed.

Registration, tax, permits and legal duties require current official evidence. Suppliers, providers and prices
must be tied to actual sources. If the web cannot establish a material fact, keep it unknown and request the
smallest useful real-world validation task. Irreversible capital/legal actions remain user-approved.

Never describe the Monte Carlo result as a universal probability that the business succeeds. It is only the
probability of satisfying the explicitly modelled cash and owner-income conditions under current assumptions.
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
