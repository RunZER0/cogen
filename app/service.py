from __future__ import annotations

import time
from datetime import UTC, datetime

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    Confidence,
    EventType,
    Evidence,
    ForkVentureRequest,
    IntakeDraft,
    IntakeDraftRequest,
    JobStatus,
    MaterialChange,
    MonitorConfigRequest,
    MonitorSchedule,
    ResearchProgress,
    SandboxExperiment,
    SandboxRequest,
    ValidationTask,
    Venture,
    VentureFork,
    VentureIntake,
)
from app.engine import VentureEngine
from app.evidence import EvidenceLedger, invalidate_dependents
from app.forks import fork_venture
from app.intake import plan_intake
from app.orchestration import SpecialistOrchestrator
from app.repository import VentureRepository
from app.research import ResearchProvider
from app.source_router import is_likely_official_authority_url
from app.sandbox import SandboxRunner
from app.state import StateStore
from app.subagents import SubagentRegistry
from app.workflow import WorkflowRunner, WorkflowStatus


class VentureService:
    def __init__(
        self,
        repository: VentureRepository,
        research_provider: ResearchProvider,
        engine: VentureEngine | None = None,
        state: StateStore | None = None,
        orchestrator: SpecialistOrchestrator | None = None,
        workflow_runner: WorkflowRunner | None = None,
        specialist_research_rounds: int = 2,
        subagent_registry: SubagentRegistry | None = None,
    ):
        self.repository = repository
        self.research_provider = research_provider
        self.engine = engine or VentureEngine()
        self.state = state or StateStore(repository)
        self.orchestrator = orchestrator or SpecialistOrchestrator(
            research_provider,
            max_rounds=specialist_research_rounds,
        )
        self.subagent_registry = subagent_registry
        self.workflow_runner = workflow_runner or WorkflowRunner(
            repository,
            self.state,
            self.orchestrator,
            self.engine,
            registry=subagent_registry,
        )
        self.sandbox = SandboxRunner(self.engine)

    def create_venture(self, intake: VentureIntake) -> Venture:
        venture = self.engine.initialise(Venture(intake=intake))
        self.repository.save_venture(venture)
        self.state.event(
            venture.id,
            EventType.VENTURE_CREATED,
            f"{venture.id}:created",
            {"idea": intake.idea, "location": intake.location},
            actor="founder",
        )
        return venture

    def list_ventures(self) -> list[Venture]:
        return self.repository.list_ventures()

    def get_venture(self, venture_id: str) -> Venture:
        venture = self.repository.get_venture(venture_id)
        if venture is None:
            raise KeyError("Venture not found")
        return venture

    def delete_venture(self, venture_id: str) -> None:
        self.get_venture(venture_id)  # raises KeyError if it doesn't exist
        self.repository.delete_venture(venture_id)

    def recompute_underwriting(self, venture_id: str) -> Venture:
        """Re-run the deterministic decision against the venture's current evidence.

        Fast and does not gather new evidence — evidence-adding methods already re-underwrite as
        part of admitting the evidence, so this exists for confirming/refreshing the decision on
        demand (e.g. from chat) without paying for a full specialist research pass, which is a
        separate, deliberately heavier action started via create_analysis_job/run_analysis_job.
        """
        venture = self.get_venture(venture_id)
        venture = self.engine.underwrite(venture)
        self.repository.save_venture(venture)
        return venture

    async def get_or_generate_narrative(self, venture_id: str) -> str:
        """The Position tab's lead narrative — generated on first read after underwriting changes
        (UnderwritingResult.narrative is None on any freshly computed result) and cached back onto
        the venture so every read after that is instant. A plain async method rather than
        something routed through the subagent runtime: FastAPI route handlers already run on the
        main event loop, so there is no sync/worker-thread boundary to cross here the way there is
        from deep inside engine.underwrite()'s own call stack."""
        from app.narrative import generate_narrative

        venture = self.get_venture(venture_id)
        if not venture.underwriting:
            return ""
        if venture.underwriting.narrative:
            return venture.underwriting.narrative
        text = await generate_narrative(venture)
        if text:
            venture.underwriting.narrative = text
            self.repository.save_venture(venture)
        return text

    # --- Founder model & recommendations (cross-venture, cross-session memory) ---

    def founder_model(self) -> dict:
        """The aggregated, persistent founder profile built from every venture on file."""
        from app.founder_model import FounderModelBuilder

        ventures = self.repository.list_ventures()
        model = FounderModelBuilder().build(ventures)
        self.state.save_founder_model(model)
        return model

    def tailoring_context(self) -> str:
        """Compact founder-model block for injecting into the agent's context (tailored, not
        sycophantic — see founder_model.build_tailoring_context)."""
        from app.founder_model import build_tailoring_context

        return build_tailoring_context(self.repository.list_ventures())

    def pivot_candidates(self, venture_id: str) -> list[dict]:
        """Validated pivot/branch candidates for a rejected/conditional venture, fit to the
        founder's demonstrated constraints."""
        from app.founder_model import pivot_candidates

        venture = self.get_venture(venture_id)
        return pivot_candidates(venture, self.repository.list_ventures())

    async def generate_recommendation(self) -> dict:
        """Run the weekly recommendation agent and persist the result."""
        from app.founder_model import generate_recommendation
        from app.domain import new_id

        ventures = self.repository.list_ventures()
        text = await generate_recommendation(ventures)
        recommendation = {
            "id": new_id(),
            "text": text,
            "created_at": datetime.now(UTC).isoformat(),
            "venture_count": len(ventures),
        }
        self.state.save_recommendation(recommendation)
        return recommendation

    def list_recommendations(self) -> list[dict]:
        return self.state.list_recommendations()

    # --- Two-tier durable memory: venture-scoped + cross-session user memory ---

    def remember_user_fact(self, key: str, fact: str, *, venture_id: str | None = None, source: str = "agent") -> dict:
        """Persist one durable, cross-session fact/preference about the founder."""
        from app.founder_model import UserMemory

        return UserMemory.remember(self.state, key, fact, venture_id=venture_id, source=source)

    def recall_user_memory(self, venture_id: str | None = None) -> list[dict]:
        """All durable user-memory facts relevant to a venture (global + that venture's own)."""
        from app.founder_model import UserMemory

        return UserMemory.recall(self.state, venture_id=venture_id)

    # --- Agent working todos (Copilot-style checklist, persisted per venture) ---

    def save_agent_todos(self, venture_id: str, todos: list[dict]) -> list[dict]:
        """Persist the agent's working todo list for a venture."""
        self.state.save_agent_todos(venture_id, todos)
        return todos

    def get_agent_todos(self, venture_id: str) -> list[dict]:
        """The agent's current working todo list for a venture (empty if none yet)."""
        return self.state.get_agent_todos(venture_id)

    def user_memory_context(self, venture_id: str | None = None) -> str:
        """Compact user-memory block for the agent's context — durable preferences and facts."""
        facts = self.recall_user_memory(venture_id)
        if not facts:
            return "USER MEMORY: nothing durable learned yet about this founder's preferences."
        lines = [f"- {f.get('fact')} (learned {f.get('updated_at', '')[:10]})" for f in facts[:12]]
        return "USER MEMORY (durable, cross-session — use to fit responses to this person, never to flatter):\n" + "\n".join(lines)

    def create_analysis_job(
        self,
        venture_id: str,
        idempotency_key: str | None = None,
    ) -> AnalysisJob:
        self.get_venture(venture_id)
        job = AnalysisJob(venture_id=venture_id, idempotency_key=idempotency_key)
        workflow_key = idempotency_key or f"analysis-job:{job.id}"
        workflow = self.workflow_runner.start(venture_id, workflow_key)
        job.workflow_id = workflow.id
        return self.repository.save_job(job)

    def run_analysis_job(self, job_id: str) -> AnalysisJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("Analysis job not found")
        if job.status == JobStatus.COMPLETE:
            return job
        if not job.workflow_id:
            workflow = self.workflow_runner.start(job.venture_id, f"analysis-job:{job.id}")
            job.workflow_id = workflow.id
        job.status = JobStatus.RUNNING
        job.message = "Running checkpointed specialist research and adversarial underwriting"
        job.attempt += 1
        job.updated_at = datetime.now(UTC)
        self.repository.save_job(job)
        t0 = time.monotonic()
        try:
            workflow = self.workflow_runner.run(job.workflow_id)
            if workflow.status == WorkflowStatus.COMPLETE:
                venture = self.get_venture(job.venture_id)
                job.status = JobStatus.COMPLETE
                decision = venture.underwriting.decision.value if venture.underwriting else "needs_data"
                job.message = f"Analysis completed with decision: {decision}"
                job.elapsed_seconds = round(time.monotonic() - t0, 2)
            elif workflow.status == WorkflowStatus.RETRYABLE:
                job.status = JobStatus.RETRYABLE
                job.message = workflow.last_error
            else:
                job.status = JobStatus.FAILED
                job.message = workflow.last_error
        except (TimeoutError, ConnectionError) as exc:
            job.status = JobStatus.RETRYABLE
            job.message = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # persist failure for async diagnosis
            job.status = JobStatus.FAILED
            job.message = f"{type(exc).__name__}: {exc}"
        job.updated_at = datetime.now(UTC)
        return self.repository.save_job(job)

    def get_job(self, job_id: str) -> AnalysisJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("Analysis job not found")
        return job

    def research_progress(self, venture_id: str) -> ResearchProgress:
        """Live read of the most recent specialist-research workflow, sourced from the same
        checkpoint records WorkflowRunner writes as it goes (WorkflowRun, SpecialistReport) — so
        this can be polled from a request handler entirely separate from the one that's actually
        running the (potentially minutes-long) workflow, without any extra tracking machinery.
        """
        self.get_venture(venture_id)
        workflows = self.state.list_workflows(venture_id)
        if not workflows:
            return ResearchProgress(status="none")
        workflow = max(workflows, key=lambda w: w.updated_at)
        reports = [
            r for r in self.state.list_specialist_reports(venture_id) if r.workflow_id == workflow.id
        ]
        end = workflow.finished_at or datetime.now(UTC)
        started = workflow.started_at or workflow.created_at
        return ResearchProgress(
            status=workflow.status.value,
            phase=workflow.phase.value,
            phases_done=[p.value for p in workflow.completed_phases],
            specialists_done=[r.role.value for r in reports],
            elapsed_seconds=round((end - started).total_seconds(), 1),
        )

    def add_evidence(self, venture_id: str, request: AddEvidenceRequest) -> Venture:
        venture = self.get_venture(venture_id)
        source_url = str(request.source_url) if request.source_url else None
        if request.evidence_type.value == "official" and not is_likely_official_authority_url(source_url):
            raise ValueError(
                "Official evidence requires a recognizable government or governing-authority URL"
            )
        # Apply the same conservative boundary to direct founder/API writes that the specialist
        # ledger already applies: a model estimate or demo fixture cannot become high-confidence
        # canonical evidence just because the caller supplied an optimistic confidence label.
        effective_confidence = request.confidence
        if request.evidence_type.value in {"model", "demo"} and request.confidence in {
            Confidence.HIGH, Confidence.VERIFIED
        }:
            effective_confidence = Confidence.LOW
        evidence = Evidence(
            assumption_key=request.assumption_key,
            claim=request.claim,
            value=request.value,
            unit=request.unit,
            evidence_type=request.evidence_type,
            confidence=effective_confidence,
            source_title=request.source_title,
            source_url=request.source_url,
            notes=request.notes,
        )
        contradictions = EvidenceLedger._contradictions(venture, evidence)
        venture = self.engine.add_evidence(venture, evidence)
        invalidated = invalidate_dependents(venture, request.assumption_key)
        # Always re-underwrite after an evidence mutation (new evidence, de-duplicated evidence,
        # or downstream invalidation): the venture's survival/decision must always reflect the
        # latest state, not a cached result from before the change. Previously this only
        # re-underwrote when `invalidated` was non-empty, which left the survival number stale
        # whenever a change updated an assumption value without invalidating a dependency —
        # exactly the observed "rent/demand change but survival frozen at 0.828" bug.
        venture = self.engine.underwrite(venture)
        self.repository.save_venture(venture)
        self.state.event(
            venture.id,
            EventType.EVIDENCE_ADDED,
            f"evidence:{evidence.fingerprint}",
            {
                "evidence_id": evidence.id,
                "assumption_key": request.assumption_key,
                "invalidated": invalidated,
            },
            actor="founder",
        )
        for key in invalidated:
            self.state.event(
                venture.id,
                EventType.ASSUMPTION_INVALIDATED,
                f"evidence:{evidence.fingerprint}:invalidated:{key}",
                {"assumption_key": key, "caused_by": request.assumption_key},
            )
        for contradiction in contradictions:
            self.state.save_contradiction(contradiction)
            self.state.event(
                venture.id,
                EventType.CONTRADICTION_DETECTED,
                f"founder:contradiction:{contradiction.id}",
                {
                    "assumption_key": contradiction.assumption_key,
                    "evidence_id_a": contradiction.evidence_id_a,
                    "evidence_id_b": contradiction.evidence_id_b,
                },
                actor="founder",
            )
        return venture

    def apply_change(self, venture_id: str, request: ApplyChangeRequest) -> Venture:
        venture = self.get_venture(venture_id)
        change = MaterialChange(
            summary=request.summary,
            assumption_key=request.assumption_key,
            new_value=request.new_value,
            source_title=request.source_title,
            source_url=request.source_url,
            confidence=request.confidence,
        )
        venture = self.engine.apply_change(venture, change)
        invalidated = (
            invalidate_dependents(venture, request.assumption_key)
            if request.assumption_key
            else []
        )
        # A material change always re-underwrites: the survival probability and decision must
        # reflect the changed assumption immediately, not wait for a downstream dependency to be
        # invalidated. This is what makes a rent hike or an actual-demand figure move the model.
        venture = self.engine.underwrite(venture)
        self.repository.save_venture(venture)
        self.state.event(
            venture.id,
            EventType.MATERIAL_CHANGE,
            f"change:{change.id}",
            {
                "summary": request.summary,
                "assumption_key": request.assumption_key,
                "old_value": change.old_value,
                "new_value": change.new_value,
                "invalidated": invalidated,
            },
        )
        return venture

    def complete_step(self, venture_id: str, step_id: str) -> Venture:
        venture = self.get_venture(venture_id)
        venture = self.engine.complete_roadmap_step(venture, step_id)
        self.repository.save_venture(venture)
        self.state.event(
            venture.id,
            EventType.ROADMAP_COMPLETED,
            f"roadmap:{step_id}:complete",
            {"step_id": step_id},
            actor="founder",
        )
        return venture

    def fork(self, venture_id: str, request: ForkVentureRequest) -> Venture:
        parent = self.get_venture(venture_id)
        child, fork = fork_venture(parent, request)
        child = self.engine.underwrite(child)
        self.repository.save_venture(child)
        self.state.save_fork(fork)
        self.state.event(
            parent.id,
            EventType.FORK_CREATED,
            f"fork:{fork.id}",
            {
                "child_venture_id": child.id,
                "label": fork.label,
                "invalidated_assumptions": fork.invalidated_assumptions,
            },
        )
        return child

    def forks(self, venture_id: str) -> list[VentureFork]:
        return self.state.list_forks(venture_id)

    def run_sandbox(self, venture_id: str, request: SandboxRequest) -> SandboxExperiment:
        venture = self.get_venture(venture_id)
        experiment = self.sandbox.run(venture, request)
        self.state.save_experiment(experiment)
        self.state.event(
            venture.id,
            EventType.EXPERIMENT_COMPLETED,
            f"experiment:{experiment.id}",
            {
                "name": experiment.name,
                "baseline_probability": experiment.baseline_probability,
                "scenario_probability": experiment.scenario_probability,
            },
        )
        return experiment

    def experiments(self, venture_id: str) -> list[SandboxExperiment]:
        return self.state.list_experiments(venture_id)

    def validation_tasks(self, venture_id: str) -> list[ValidationTask]:
        return self.state.list_validation_tasks(venture_id)

    def events(self, venture_id: str):
        return self.state.list_events(venture_id)

    def contradictions(self, venture_id: str):
        return self.state.list_contradictions(venture_id)

    def specialists(self, venture_id: str):
        return self.state.list_specialist_reports(venture_id)

    def plan_intake(self, request: IntakeDraftRequest, draft_id: str | None = None) -> IntakeDraft:
        existing = self.state.get_intake_draft(draft_id) if draft_id else None
        draft = plan_intake(request, existing)
        return self.state.save_intake_draft(draft)

    def readiness(self) -> dict[str, object]:
        return {
            "database": "ok" if self.repository.ping() else "failed",
            "persistent_state": True,
            "research_runtime": self.research_provider.runtime_health(),
        }

    def timeline(self, venture_id: str) -> list[dict]:
        """Return the venture event log as a human-readable timeline with per-event elapsed deltas."""
        events = sorted(self.state.list_events(venture_id), key=lambda e: e.occurred_at)
        result = []
        base: datetime | None = None
        for event in events:
            ts = event.occurred_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if base is None:
                base = ts
            elapsed = round((ts - base).total_seconds(), 3)
            entry = {
                "elapsed_seconds": elapsed,
                "event_type": event.event_type.value,
                "actor": event.actor,
                "occurred_at": ts.isoformat(),
                "payload": event.payload,
            }
            result.append(entry)
        return result

    def configure_monitor(
        self,
        venture_id: str,
        request: MonitorConfigRequest,
    ) -> MonitorSchedule:
        """Create or update the monitor schedule for a venture."""
        self.get_venture(venture_id)  # raises KeyError if not found
        existing = self.state.get_monitor_schedule(venture_id)
        if existing is None:
            schedule = MonitorSchedule(
                venture_id=venture_id,
                enabled=request.enabled,
                interval_hours=request.interval_hours,
            )
        else:
            existing.enabled = request.enabled
            existing.interval_hours = request.interval_hours
            existing.updated_at = datetime.now(UTC)
            schedule = existing
        return self.state.save_monitor_schedule(schedule)

    def get_monitor_schedule(self, venture_id: str) -> MonitorSchedule | None:
        return self.state.get_monitor_schedule(venture_id)

    def create_demo_venture(self) -> tuple[Venture, AnalysisJob]:
        intake = VentureIntake.model_validate(
            {
                "idea": "Open a neighbourhood supermarket/minimart",
                "business_type": "supermarket retail",
                "location": "Ruiru, Kiambu County, Kenya",
                "locality": "Ruiru",
                "subdivision": "Kiambu County",
                "country": "Kenya",
                "currency": "KES",
                "locale": "en-KE",
                "launch_target_months": 4,
                "founder": {
                    "available_capital": 1_800_000,
                    "protected_reserve": 150_000,
                    "debt_available": 0,
                    "target_monthly_owner_income": 120_000,
                    "max_acceptable_loss": 600_000,
                    "time_commitment": "full-time",
                    "experience": "first-time retail founder",
                },
                "notes": (
                    "Canonical hackathon scenario. Offline mode uses clearly labelled fixtures; "
                    "live mode uses Gemini-grounded specialist research."
                ),
            }
        )
        venture = self.create_venture(intake)
        job = self.create_analysis_job(venture.id)
        return venture, job

    def demo_venture(self) -> Venture:
        venture, job = self.create_demo_venture()
        self.run_analysis_job(job.id)
        return self.get_venture(venture.id)
