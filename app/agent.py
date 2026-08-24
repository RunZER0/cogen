"""Google ADK entrypoint.

The ADK agent is deliberately a thin reasoning/orchestration layer over a structured venture state engine.
It can inspect or mutate persistent ventures through tools instead of hiding state inside chat history.
"""

from __future__ import annotations

import json

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.domain import AddEvidenceRequest, ApplyChangeRequest, VentureIntake
from app.runtime import get_service
from app.settings import get_settings


def create_venture(intake_json: str) -> str:
    """Create a persistent venture from a JSON object containing idea, location and founder primitives."""
    intake = VentureIntake.model_validate(json.loads(intake_json))
    venture = get_service().create_venture(intake)
    return venture.model_dump_json(indent=2)


def inspect_venture(venture_id: str) -> str:
    """Read the current venture twin, including assumptions, evidence, decision and execution roadmap."""
    return get_service().get_venture(venture_id).model_dump_json(indent=2)


def run_underwriting(venture_id: str) -> str:
    """Run grounded/offline research and adversarial underwriting for an existing venture."""
    service = get_service()
    job = service.create_analysis_job(venture_id)
    completed = service.run_analysis_job(job.id)
    return completed.model_dump_json(indent=2)


def add_founder_evidence(venture_id: str, evidence_json: str) -> str:
    """Add observed or founder-supplied evidence to a specific assumption and re-underwrite."""
    request = AddEvidenceRequest.model_validate(json.loads(evidence_json))
    return get_service().add_evidence(venture_id, request).model_dump_json(indent=2)


def apply_material_change(venture_id: str, change_json: str) -> str:
    """Apply a changed cost, demand, competitor or regulatory fact and recompute the venture model."""
    request = ApplyChangeRequest.model_validate(json.loads(change_json))
    return get_service().apply_change(venture_id, request).model_dump_json(indent=2)


def complete_execution_step(venture_id: str, step_id: str) -> str:
    """Mark a currently unlocked roadmap step complete and unlock downstream dependencies."""
    return get_service().complete_step(venture_id, step_id).model_dump_json(indent=2)


settings = get_settings()
root_agent = Agent(
    name="venture_underwriter",
    model=Gemini(
        model=settings.gemini_model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""
You are the persistent adversarial venture underwriter and execution partner.

Your objective is to stop the user learning expensive business facts after committing capital.
Do not flatter an idea. First establish the founder's concrete primitives: available and protected capital,
location, income target, time commitment, launch target, acceptable loss and relevant constraints. Persist
ideas with create_venture. Use run_underwriting to research/attack them. Treat weak evidence as weak; never
promote an assumption merely because it sounds plausible. A conditional or rejected decision is useful.

When a venture survives, guide the user through the stored roadmap. Registration, tax, permits, legal duties,
suppliers, providers and prices must be tied to current evidence; do not fabricate official requirements or
people. Irreversible actions remain human-approved. If a material fact changes, call apply_material_change
and reason from the new structured state rather than from stale conversation memory.

Never convert the simulation into a universal "chance this business succeeds". Explain that it is the chance
of satisfying the explicitly modelled cash/break-even conditions under current assumptions and confidence.
""".strip(),
    tools=[
        create_venture,
        inspect_venture,
        run_underwriting,
        add_founder_evidence,
        apply_material_change,
        complete_execution_step,
    ],
)

app = App(root_agent=root_agent, name="app")
