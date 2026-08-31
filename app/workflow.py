from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime

from app.domain import (
    Confidence,
    EventType,
    EvidenceType,
    ResearchBatch,
    SpecialistRole,
    SubagentKind,
    WorkflowPhase,
    WorkflowRun,
    WorkflowStatus,
)
from app.engine import VentureEngine
from app.orchestration import SpecialistOrchestrator
from app.repository import VentureRepository
from app.research import ResearchFinding
from app.state import StateStore
from app.subagents import SubagentRegistry
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

    Completed phases are durably recorded. Research also checkpoints after each specialist, so a retry
    does not pay to rerun roles whose report and candidate-evidence batch are already persisted.
    """

    def __init__(
        self,
        repository: VentureRepository,
        state: StateStore,
        orchestrator: SpecialistOrchestrator,
        engine: VentureEngine | None = None,
        registry: SubagentRegistry | None = None,
    ):
        self.repository = repository
        self.state = state
        self.orchestrator = orchestrator
        self.engine = engine or VentureEngine()
        # Optional: when wired (see app/runtime.py), each specialist's run_role becomes a durable,
        # individually-inspectable SubagentRun instead of an opaque loop iteration — see
        # _run_research below. None in most direct unit-test construction, which keeps today's
        # exact behavior (no emit, no SubagentRun row) rather than requiring every test to know
        # about the subagent runtime.
        self.registry = registry

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
        stop_after_specialist: SpecialistRole | None = None,
    ) -> WorkflowRun:
        workflow = self.state.get_workflow(workflow_id)
        if workflow is None:
            raise KeyError("Workflow not found")
        if workflow.status == WorkflowStatus.COMPLETE:
            return workflow

        run_start = time.monotonic()
        now_utc = datetime.now(UTC)
        workflow.status = WorkflowStatus.RUNNING
        workflow.attempt += 1
        workflow.last_error = None
        workflow.updated_at = now_utc
        # Stamp started_at only on the very first attempt
        if workflow.started_at is None:
            workflow.started_at = now_utc
        self.state.save_workflow(workflow)

        try:
            for phase in PHASE_ORDER:
                if phase in workflow.completed_phases:
                    continue
                workflow.phase = phase
                # Persisted immediately, not just after the phase finishes — RESEARCH alone can run
                # for minutes, and a live progress poll during that window (app/service.py's
                # research_progress) needs to see the phase actually in flight, not whatever the
                # last-completed phase happened to leave behind.
                self.state.save_workflow(workflow)
                phase_start = time.monotonic()
                self._run_phase(
                    workflow,
                    phase,
                    stop_after_specialist=stop_after_specialist,
                )
                phase_elapsed = round(time.monotonic() - phase_start, 3)
                workflow.phase_timings[phase.value] = (
                    workflow.phase_timings.get(phase.value, 0.0) + phase_elapsed
                )
                workflow.completed_phases.append(phase)
                workflow.updated_at = datetime.now(UTC)
                self.state.save_workflow(workflow)
                self.state.event(
                    workflow.venture_id,
                    EventType.WORKFLOW_CHECKPOINT,
                    f"{workflow.id}:checkpoint:{phase.value}",
                    {
                        "workflow_id": workflow.id,
                        "phase": phase.value,
                        "phase_elapsed_seconds": phase_elapsed,
                        "total_elapsed_seconds": round(time.monotonic() - run_start, 3),
                    },
                )
                if stop_after_phase == phase:
                    raise SimulatedCrash(f"simulated process death after {phase.value}")

            workflow.phase = WorkflowPhase.COMPLETE
            workflow.status = WorkflowStatus.COMPLETE
            workflow.finished_at = datetime.now(UTC)
            workflow.updated_at = workflow.finished_at
            return self.state.save_workflow(workflow)
        except SimulatedCrash as exc:
            workflow.status = WorkflowStatus.RETRYABLE
            workflow.last_error = str(exc)
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

    def _run_phase(
        self,
        workflow: WorkflowRun,
        phase: WorkflowPhase,
        *,
        stop_after_specialist: SpecialistRole | None = None,
    ) -> None:
        venture = self.repository.get_venture(workflow.venture_id)
        if venture is None:
            raise KeyError("Venture not found")

        if phase == WorkflowPhase.PLAN:
            return

        if phase == WorkflowPhase.RESEARCH:
            self._run_research(
                workflow,
                venture,
                stop_after_specialist=stop_after_specialist,
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

    def _run_research(
        self,
        workflow: WorkflowRun,
        venture,
        *,
        stop_after_specialist: SpecialistRole | None,
    ) -> None:
        reports = [
            report
            for report in self.state.list_specialist_reports(venture.id)
            if report.workflow_id == workflow.id
        ]
        completed_roles = {report.role for report in reports}
        batch = self.state.research_batch_for_workflow(venture.id, workflow.id)
        if batch is None:
            batch = ResearchBatch(
                id=f"{workflow.id}:research",
                venture_id=venture.id,
                workflow_id=workflow.id,
            )

        seen = {
            self._finding_key(self._finding_from_dict(raw))
            for raw in batch.findings
        }

        for role in self.orchestrator.roles:
            if role in completed_roles:
                continue
            # Build a confirmed-findings digest from everything admitted so far in this workflow.
            # This is passed into run_role so the orchestrator can inject it into follow-up mandates,
            # preventing the model from re-searching already-confirmed facts.
            confirmed_context = self._build_confirmed_context(batch.findings, role)

            def do_role(emit, role=role, confirmed_context=confirmed_context):
                return self.orchestrator.run_role(
                    venture, workflow.id, role, seen=seen,
                    confirmed_context=confirmed_context, emit=emit,
                )

            if self.registry:
                def wrap_emit(inner_emit, batch=batch):
                    def emit_and_checkpoint(event_type: str, **fields):
                        # A round finishing means fields["findings"] just passed the ledger's
                        # admissibility gate and dedupe, so folding it into the batch right now —
                        # rather than waiting for run_role to return in full — means a crash
                        # partway through round 2 no longer loses round 1's already-accepted
                        # findings, which is today's actual gap: batch.findings previously only
                        # grew once, after the whole role finished.
                        if event_type == "round_checkpoint":
                            batch.findings.extend(fields.get("findings", []))
                            batch.rejected.extend(fields.get("rejected", []))
                            batch.rejected[:] = list(dict.fromkeys(batch.rejected))
                            self.state.save_research_batch(batch)
                        inner_emit(event_type, **fields)

                    return emit_and_checkpoint

                # Routed through run_inline (not launch): this specialist still executes right
                # here, synchronously, in this exact loop order — only its narration and
                # round-level progress become a durable, inspectable SubagentRun. See
                # app/subagents.py's module docstring for why specialists stay sequential.
                _, result = self.registry.run_inline(
                    SubagentKind.SPECIALIST,
                    venture.id,
                    workflow_id=workflow.id,
                    role=role,
                    work=do_role,
                    input_payload={"role": role.value},
                    wrap_emit=wrap_emit,
                )
                # batch.findings/rejected were already folded in incrementally via
                # round_checkpoint above — extending again here would double-count every finding.
            else:
                result = do_role(None)
                batch.findings.extend(asdict(item) for item in result.findings)
                batch.rejected.extend(result.rejected)
                batch.rejected = list(dict.fromkeys(batch.rejected))

            # run_role receives and mutates the shared `seen` set while deciding which candidates
            # are fresh — true in both branches above, since `seen` is the same object either way.
            self.state.save_research_batch(batch)

            report = result.reports[0]
            report.id = f"{workflow.id}:specialist:{role.value}"
            self.state.save_specialist_report(report)
            self.state.event(
                venture.id,
                EventType.SPECIALIST_COMPLETED,
                f"{workflow.id}:specialist:{role.value}",
                {
                    "role": role.value,
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

            if stop_after_specialist == role:
                raise SimulatedCrash(f"simulated process death after specialist {role.value}")

    @staticmethod
    def _finding_key(item: ResearchFinding) -> tuple:
        return (
            item.assumption_key,
            item.claim.strip().lower(),
            item.value,
            item.source_url,
        )

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

    @staticmethod
    def _build_confirmed_context(findings: list[dict], current_role: SpecialistRole) -> str | None:
        """Build a compact digest of already-admitted findings for the upcoming specialist.

        Injected into follow-up mandates so the model doesn't re-search confirmed facts.
        Only includes findings with medium-or-better confidence to keep the context tight.
        Returns None when there are no useful confirmed findings yet.
        """
        strong = [
            f for f in findings
            if Confidence(f.get("confidence", "unknown")) in {
                Confidence.MEDIUM, Confidence.HIGH, Confidence.VERIFIED
            }
        ]
        if not strong:
            return None
        lines = [f"CONFIRMED FINDINGS FROM PRIOR SPECIALISTS ({len(strong)} items):"]
        for f in strong[:20]:  # cap to avoid prompt bloat
            role_tag = f.get("role", "?")
            lines.append(
                f"  [{role_tag}] {f.get('assumption_key','?')}: {f.get('claim','')[:120]} "
                f"(conf={f.get('confidence','?')}, src={f.get('source_title','?')[:60]})"
            )
        lines.append(
            "Do NOT re-research these confirmed findings. Seek new or stronger evidence on unresolved assumptions."
        )
        return "\n".join(lines)
