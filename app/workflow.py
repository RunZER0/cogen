from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from app.domain import (
    Confidence,
    EventType,
    EvidenceType,
    ResearchBatch,
    SpecialistRole,
    WorkflowPhase,
    WorkflowRun,
    WorkflowStatus,
)
from app.engine import VentureEngine
from app.orchestration import SpecialistOrchestrator
from app.repository import VentureRepository
from app.research import ResearchFinding
from app.state import StateStore
from app.validation import design_validation_tasks


PHASE_ORDER = [
    WorkflowPhase.PLAN,
    WorkflowPhase.RESEARCH,
    WorkflowPhase.SYNTHESIS,
    WorkflowPhase.UNDERWRITE,
    WorkflowPhase.VALIDATION,
    WorkflowPhase.MONITOR,
]


class SimulatedCrash(RuntimeError):
    pass


class WorkflowRunner:
    """Checkpointed, idempotent analysis workflow.

    Every completed phase is durably recorded. A retry with the same workflow resumes at the first
    incomplete phase instead of recreating the venture or replaying completed state writes.
    """

    def __init__(
        self,
        repository: VentureRepository,
        state: StateStore,
        orchestrator: SpecialistOrchestrator,
        engine: VentureEngine | None = None,
    ):
        self.repository = repository
        self.state = state
        self.orchestrator = orchestrator
        self.engine = engine or VentureEngine()

    def start(self, venture_id: str, idempotency_key: str) -> WorkflowRun:
        existing = self.state.workflow_by_key(venture_id, idempotency_key)
        if existing:
            return existing
        workflow = WorkflowRun(venture_id=venture_id, idempotency_key=idempotency_key)
        self.state.save_workflow(workflow)
        self.state.event(
            venture_id,
            EventType.WORKFLOW_STARTED,
            f"{workflow.id}:started",
            {"workflow_id": workflow.id},
        )
        return workflow

    def run(
        self,
        workflow_id: str,
        *,
        stop_after_phase: WorkflowPhase | None = None,
    ) -> WorkflowRun:
        workflow = self.state.get_workflow(workflow_id)
        if workflow is None:
            raise KeyError("Workflow not found")
        if workflow.status == WorkflowStatus.COMPLETE:
            return workflow

        workflow.status = WorkflowStatus.RUNNING
        workflow.attempt += 1
        workflow.last_error = None
        workflow.updated_at = datetime.now(UTC)
        self.state.save_workflow(workflow)

        try:
            for phase in PHASE_ORDER:
                if phase in workflow.completed_phases:
                    continue
                workflow.phase = phase
                self._run_phase(workflow, phase)
                workflow.completed_phases.append(phase)
                workflow.updated_at = datetime.now(UTC)
                self.state.save_workflow(workflow)
                self.state.event(
                    workflow.venture_id,
                    EventType.WORKFLOW_CHECKPOINT,
                    f"{workflow.id}:checkpoint:{phase.value}",
                    {"workflow_id": workflow.id, "phase": phase.value},
                )
                if stop_after_phase == phase:
                    raise SimulatedCrash(f"simulated process death after {phase.value}")

            workflow.phase = WorkflowPhase.COMPLETE
            workflow.status = WorkflowStatus.COMPLETE
            workflow.updated_at = datetime.now(UTC)
            return self.state.save_workflow(workflow)
        except SimulatedCrash:
            workflow.status = WorkflowStatus.RETRYABLE
            workflow.last_error = "simulated process death"
            workflow.updated_at = datetime.now(UTC)
            self.state.save_workflow(workflow)
            raise
        except (TimeoutError, ConnectionError) as exc:
            workflow.status = WorkflowStatus.RETRYABLE
            workflow.last_error = f"{type(exc).__name__}: {exc}"
            workflow.updated_at = datetime.now(UTC)
            self.state.save_workflow(workflow)
            raise
        except Exception as exc:
            workflow.status = WorkflowStatus.FAILED
            workflow.last_error = f"{type(exc).__name__}: {exc}"
            workflow.updated_at = datetime.now(UTC)
            self.state.save_workflow(workflow)
            raise

    def _run_phase(self, workflow: WorkflowRun, phase: WorkflowPhase) -> None:
        venture = self.repository.get_venture(workflow.venture_id)
        if venture is None:
            raise KeyError("Venture not found")

        if phase == WorkflowPhase.PLAN:
            return

        if phase == WorkflowPhase.RESEARCH:
            result = self.orchestrator.run(venture, workflow.id)
            batch = ResearchBatch(
                id=f"{workflow.id}:research",
                venture_id=venture.id,
                workflow_id=workflow.id,
                findings=[asdict(item) for item in result.findings],
                rejected=result.rejected,
            )
            self.state.save_research_batch(batch)
            for report in result.reports:
                self.state.save_specialist_report(report)
                self.state.event(
                    venture.id,
                    EventType.SPECIALIST_COMPLETED,
                    f"{workflow.id}:specialist:{report.role.value}",
                    {
                        "role": report.role.value,
                        "finding_count": report.finding_count,
                        "rejected_count": report.rejected_count,
                    },
                )
            for contradiction in result.contradictions:
                self.state.save_contradiction(contradiction)
                self.state.event(
                    venture.id,
                    EventType.CONTRADICTION_DETECTED,
                    f"{workflow.id}:contradiction:{contradiction.id}",
                    {"assumption_key": contradiction.assumption_key},
                )
            return

        if phase == WorkflowPhase.SYNTHESIS:
            batch = self.state.research_batch_for_workflow(venture.id, workflow.id)
            if batch is None:
                raise RuntimeError("Research checkpoint missing")
            findings = [self._finding_from_dict(item) for item in batch.findings]
            venture = self.engine.ingest_research(venture, findings)
            self.repository.save_venture(venture)
            self.state.event(
                venture.id,
                EventType.EVIDENCE_ADDED,
                f"{workflow.id}:evidence-ingested",
                {"finding_count": len(findings), "rejected_count": len(batch.rejected)},
            )
            return

        if phase == WorkflowPhase.UNDERWRITE:
            venture = self.engine.underwrite(venture)
            self.repository.save_venture(venture)
            result = venture.underwriting
            self.state.event(
                venture.id,
                EventType.UNDERWRITING_COMPLETED,
                f"{workflow.id}:underwriting",
                {
                    "decision": result.decision.value if result else None,
                    "probability": result.break_even_probability_12m if result else None,
                },
            )
            return

        if phase == WorkflowPhase.VALIDATION:
            for task in design_validation_tasks(venture):
                task.id = f"{workflow.id}:validation:{task.assumption_key}"
                self.state.save_validation_task(task)
                self.state.event(
                    venture.id,
                    EventType.VALIDATION_REQUIRED,
                    f"{workflow.id}:validation:{task.assumption_key}",
                    {"assumption_key": task.assumption_key, "title": task.title},
                )
            return

        if phase == WorkflowPhase.MONITOR:
            return

    @staticmethod
    def _finding_from_dict(raw: dict) -> ResearchFinding:
        role = raw.get("role")
        if role and not isinstance(role, SpecialistRole):
            role = SpecialistRole(role)
        evidence_type = raw.get("evidence_type")
        if not isinstance(evidence_type, EvidenceType):
            evidence_type = EvidenceType(evidence_type)
        confidence = raw.get("confidence")
        if not isinstance(confidence, Confidence):
            confidence = Confidence(confidence)
        return ResearchFinding(
            assumption_key=raw["assumption_key"],
            claim=raw["claim"],
            value=raw.get("value"),
            unit=raw.get("unit"),
            evidence_type=evidence_type,
            confidence=confidence,
            source_title=raw["source_title"],
            source_url=raw.get("source_url"),
            notes=raw.get("notes"),
            role=role,
        )
