from __future__ import annotations

from datetime import UTC, datetime

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    EventType,
    Evidence,
    ForkVentureRequest,
    IntakeDraft,
    IntakeDraftRequest,
    JobStatus,
    MaterialChange,
    SandboxExperiment,
    SandboxRequest,
    ValidationTask,
    Venture,
    VentureFork,
    VentureIntake,
)
from app.engine import VentureEngine
from app.evidence import invalidate_dependents
from app.forks import fork_venture
from app.intake import plan_intake
from app.orchestration import SpecialistOrchestrator
from app.repository import VentureRepository
from app.research import ResearchProvider
from app.sandbox import SandboxRunner
from app.state import StateStore
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
    ):
        self.repository = repository
        self.research_provider = research_provider
        self.engine = engine or VentureEngine()
        self.state = state or StateStore(repository)
        self.orchestrator = orchestrator or SpecialistOrchestrator(
            research_provider,
            max_rounds=specialist_research_rounds,
        )
        self.workflow_runner = workflow_runner or WorkflowRunner(
            repository,
            self.state,
            self.orchestrator,
            self.engine,
        )
        self.sandbox = SandboxRunner(self.engine)

    def create_venture(self, intake: VentureIntake) -> Venture:
        venture = self.engine.initialise(Venture(intake=intake))
        self.repository.save_venture(venture)
        self.state.event(
            venture.id,
            EventType.VENTURE_CREATED,
            f"{venture.id}:created",
            {
                "idea": intake.idea,
                "location": intake.location,
                "jurisdiction": intake.jurisdiction.model_dump(mode="json"),
            },
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
        try:
            workflow = self.workflow_runner.run(job.workflow_id)
            if workflow.status == WorkflowStatus.COMPLETE:
                venture = self.get_venture(job.venture_id)
                job.status = JobStatus.COMPLETE
                decision = venture.underwriting.decision.value if venture.underwriting else "needs_data"
                job.message = f"Analysis completed with decision: {decision}"
            elif workflow.status == WorkflowStatus.RETRYABLE:
                job.status = JobStatus.RETRYABLE
                job.message = workflow.last_error
            else:
                job.status = JobStatus.FAILED
                job.message = workflow.last_error
        except (TimeoutError, ConnectionError) as exc:
            job.status = JobStatus.RETRYABLE
            job.message = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.message = f"{type(exc).__name__}: {exc}"
        job.updated_at = datetime.now(UTC)
        return self.repository.save_job(job)

    def get_job(self, job_id: str) -> AnalysisJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("Analysis job not found")
        return job

    def add_evidence(self, venture_id: str, request: AddEvidenceRequest) -> Venture:
        venture = self.get_venture(venture_id)
        evidence = Evidence(
            assumption_key=request.assumption_key,
            claim=request.claim,
            value=request.value,
            unit=request.unit,
            evidence_type=request.evidence_type,
            confidence=request.confidence,
            source_title=request.source_title,
            source_url=request.source_url,
            notes=request.notes,
        )
        venture = self.engine.add_evidence(venture, evidence)
        invalidated = invalidate_dependents(venture, request.assumption_key)
        if invalidated:
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
        invalidated = invalidate_dependents(venture, request.assumption_key) if request.assumption_key else []
        if invalidated:
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

    def demo_venture(self) -> Venture:
        intake = VentureIntake.model_validate(
            {
                "idea": "Open a neighbourhood supermarket/minimart",
                "business_type": "supermarket retail",
                "location": "Ruiru, Kiambu County, Kenya",
                "jurisdiction": {
                    "country_code": "KE",
                    "country_name": "Kenya",
                    "subdivision": "Kiambu County",
                    "locality": "Ruiru",
                    "currency_code": "KES",
                    "locale": "en-KE",
                    "regulatory_scope": ["Kenya", "Kiambu County"],
                },
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
        self.run_analysis_job(job.id)
        return self.get_venture(venture.id)
