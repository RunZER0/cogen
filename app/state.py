from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TypeVar

from pydantic import BaseModel

from app.domain import (
    ContradictionRecord,
    EventType,
    IntakeDraft,
    MonitorSchedule,
    ResearchBatch,
    SandboxExperiment,
    SpecialistReport,
    SubagentEvent,
    SubagentKind,
    SubagentRun,
    ValidationTask,
    VentureEvent,
    VentureFork,
    WorkflowRun,
)
from app.repository import VentureRepository

T = TypeVar("T", bound=BaseModel)


class StateStore:
    """Typed facade over durable state records.

    Chat history is never the source of truth. This store persists the audit/event layer independently
    from the mutable venture snapshot so workflows can recover after process death.
    """

    KIND_EVENT = "event"
    KIND_WORKFLOW = "workflow"
    KIND_SPECIALIST = "specialist_report"
    KIND_RESEARCH_BATCH = "research_batch"
    KIND_CONTRADICTION = "contradiction"
    KIND_VALIDATION = "validation_task"
    KIND_FORK = "fork"
    KIND_EXPERIMENT = "experiment"
    KIND_INTAKE = "intake_draft"
    KIND_MONITOR = "monitor_schedule"
    KIND_SUBAGENT_RUN = "subagent_run"
    KIND_SUBAGENT_EVENT = "subagent_event"
    KIND_FOUNDER_MODEL = "founder_model"
    KIND_RECOMMENDATION = "recommendation"
    KIND_AGENT_TODOS = "agent_todos"

    def __init__(self, repository: VentureRepository):
        self.repository = repository

    def append_event(self, event: VentureEvent) -> VentureEvent:
        existing = self.repository.get_state_record_by_idempotency(
            self.KIND_EVENT,
            event.venture_id,
            event.idempotency_key,
        )
        if existing:
            return VentureEvent.model_validate_json(existing)
        self.repository.save_state_record(
            self.KIND_EVENT,
            event.id,
            event.venture_id,
            event.model_dump_json(),
            idempotency_key=event.idempotency_key,
        )
        return event

    def event(
        self,
        venture_id: str,
        event_type: EventType,
        idempotency_key: str,
        payload: dict | None = None,
        actor: str = "system",
    ) -> VentureEvent:
        return self.append_event(
            VentureEvent(
                venture_id=venture_id,
                event_type=event_type,
                idempotency_key=idempotency_key,
                payload=payload or {},
                actor=actor,
            )
        )

    def list_events(self, venture_id: str) -> list[VentureEvent]:
        return self._list(self.KIND_EVENT, venture_id, VentureEvent)

    def save_workflow(self, workflow: WorkflowRun) -> WorkflowRun:
        self.repository.save_state_record(
            self.KIND_WORKFLOW,
            workflow.id,
            workflow.venture_id,
            workflow.model_dump_json(),
            idempotency_key=workflow.idempotency_key,
        )
        return workflow

    def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        return self._get(self.KIND_WORKFLOW, workflow_id, WorkflowRun)

    def workflow_by_key(self, venture_id: str, idempotency_key: str) -> WorkflowRun | None:
        payload = self.repository.get_state_record_by_idempotency(
            self.KIND_WORKFLOW,
            venture_id,
            idempotency_key,
        )
        return WorkflowRun.model_validate_json(payload) if payload else None

    def list_workflows(self, venture_id: str) -> list[WorkflowRun]:
        return self._list(self.KIND_WORKFLOW, venture_id, WorkflowRun)

    def save_research_batch(self, batch: ResearchBatch) -> ResearchBatch:
        return self._save(self.KIND_RESEARCH_BATCH, batch.id, batch.venture_id, batch)

    def research_batch_for_workflow(self, venture_id: str, workflow_id: str) -> ResearchBatch | None:
        for batch in self._list(self.KIND_RESEARCH_BATCH, venture_id, ResearchBatch):
            if batch.workflow_id == workflow_id:
                return batch
        return None

    def save_specialist_report(self, report: SpecialistReport) -> SpecialistReport:
        return self._save(self.KIND_SPECIALIST, report.id, report.venture_id, report)

    def list_specialist_reports(self, venture_id: str) -> list[SpecialistReport]:
        return self._list(self.KIND_SPECIALIST, venture_id, SpecialistReport)

    def save_contradiction(self, item: ContradictionRecord) -> ContradictionRecord:
        return self._save(self.KIND_CONTRADICTION, item.id, item.venture_id, item)

    def list_contradictions(self, venture_id: str) -> list[ContradictionRecord]:
        return self._list(self.KIND_CONTRADICTION, venture_id, ContradictionRecord)

    def save_validation_task(self, task: ValidationTask) -> ValidationTask:
        return self._save(self.KIND_VALIDATION, task.id, task.venture_id, task)

    def list_validation_tasks(self, venture_id: str) -> list[ValidationTask]:
        return self._list(self.KIND_VALIDATION, venture_id, ValidationTask)

    def save_fork(self, fork: VentureFork) -> VentureFork:
        return self._save(self.KIND_FORK, fork.id, fork.parent_venture_id, fork)

    def list_forks(self, parent_venture_id: str) -> list[VentureFork]:
        return self._list(self.KIND_FORK, parent_venture_id, VentureFork)

    def save_experiment(self, experiment: SandboxExperiment) -> SandboxExperiment:
        return self._save(self.KIND_EXPERIMENT, experiment.id, experiment.venture_id, experiment)

    def list_experiments(self, venture_id: str) -> list[SandboxExperiment]:
        return self._list(self.KIND_EXPERIMENT, venture_id, SandboxExperiment)

    def save_intake_draft(self, draft: IntakeDraft) -> IntakeDraft:
        return self._save(self.KIND_INTAKE, draft.id, draft.id, draft)

    def get_intake_draft(self, draft_id: str) -> IntakeDraft | None:
        return self._get(self.KIND_INTAKE, draft_id, IntakeDraft)

    def save_monitor_schedule(self, schedule: MonitorSchedule) -> MonitorSchedule:
        """Upsert the monitor schedule for a venture (one schedule per venture)."""
        return self._save(self.KIND_MONITOR, schedule.venture_id, schedule.venture_id, schedule)

    def get_monitor_schedule(self, venture_id: str) -> MonitorSchedule | None:
        return self._get(self.KIND_MONITOR, venture_id, MonitorSchedule)

    def list_monitor_schedules(self) -> list[MonitorSchedule]:
        """Return all monitor schedules across all ventures."""
        payloads: Iterable[str] = self.repository.list_all_state_records(self.KIND_MONITOR)
        return [MonitorSchedule.model_validate_json(payload) for payload in payloads]

    def save_subagent_run(self, run: SubagentRun) -> SubagentRun:
        return self._save(self.KIND_SUBAGENT_RUN, run.id, run.venture_id, run)

    def get_subagent_run(self, run_id: str) -> SubagentRun | None:
        return self._get(self.KIND_SUBAGENT_RUN, run_id, SubagentRun)

    def list_subagent_runs(self, venture_id: str, kind: SubagentKind | None = None) -> list[SubagentRun]:
        runs = self._list(self.KIND_SUBAGENT_RUN, venture_id, SubagentRun)
        if kind is not None:
            runs = [r for r in runs if r.kind == kind]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def list_all_subagent_runs(self) -> list[SubagentRun]:
        """Return every subagent run across all ventures — used only by boot-time crash recovery,
        which has no single venture_id to scope the scan to."""
        payloads: Iterable[str] = self.repository.list_all_state_records(self.KIND_SUBAGENT_RUN)
        return [SubagentRun.model_validate_json(payload) for payload in payloads]

    def append_subagent_event(self, event: SubagentEvent) -> SubagentEvent:
        self.repository.save_state_record(
            self.KIND_SUBAGENT_EVENT, event.id, event.venture_id, event.model_dump_json(),
        )
        return event

    def list_subagent_events(self, venture_id: str, run_id: str) -> list[SubagentEvent]:
        events = self._list(self.KIND_SUBAGENT_EVENT, venture_id, SubagentEvent)
        return sorted((e for e in events if e.run_id == run_id), key=lambda e: e.seq)

    # --- Founder model & recommendations (cross-venture, cross-session memory) ---

    def save_founder_model(self, payload: dict) -> None:
        """Persist the aggregated founder profile as a durable state record (kind founder_model)."""
        self.repository.save_state_record(
            self.KIND_FOUNDER_MODEL,
            "founder",
            "founder",
            json.dumps(payload, default=str),
            idempotency_key="founder_model:latest",
        )

    def get_founder_model(self) -> dict | None:
        raw = self.repository.get_state_record(self.KIND_FOUNDER_MODEL, "founder")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def save_recommendation(self, recommendation: dict) -> None:
        """Persist the latest weekly recommendation (kind recommendation)."""
        self.repository.save_state_record(
            self.KIND_RECOMMENDATION,
            recommendation.get("id", "latest"),
            "founder",
            json.dumps(recommendation, default=str),
            idempotency_key=f"recommendation:{recommendation.get('id', 'latest')}",
        )

    def list_recommendations(self) -> list[dict]:
        payloads: Iterable[str] = self.repository.list_all_state_records(self.KIND_RECOMMENDATION)
        out = []
        for payload in payloads:
            try:
                out.append(json.loads(payload))
            except (TypeError, ValueError):
                continue
        return sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)

    def save_agent_todos(self, venture_id: str, todos: list[dict]) -> None:
        """Persist the agent's working todo list for a venture (kind agent_todos)."""
        self.repository.save_state_record(
            self.KIND_AGENT_TODOS,
            venture_id,
            venture_id,
            json.dumps({"venture_id": venture_id, "todos": todos}, default=str),
            idempotency_key=f"agent_todos:{venture_id}",
        )

    def get_agent_todos(self, venture_id: str) -> list[dict]:
        raw = self.repository.get_state_record(self.KIND_AGENT_TODOS, venture_id)
        if not raw:
            return []
        try:
            return json.loads(raw).get("todos", [])
        except (TypeError, ValueError):
            return []

    def _save(self, kind: str, record_id: str, venture_id: str, model: T) -> T:
        self.repository.save_state_record(kind, record_id, venture_id, model.model_dump_json())
        return model

    def _get(self, kind: str, record_id: str, model: type[T]) -> T | None:
        payload = self.repository.get_state_record(kind, record_id)
        return model.model_validate_json(payload) if payload else None

    def _list(self, kind: str, venture_id: str, model: type[T]) -> list[T]:
        payloads: Iterable[str] = self.repository.list_state_records(kind, venture_id)
        return [model.model_validate_json(payload) for payload in payloads]
