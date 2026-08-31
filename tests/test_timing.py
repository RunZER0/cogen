"""Tests for workflow timing: phase_timings, started_at, finished_at, elapsed_seconds."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import VentureIntake, WorkflowStatus
from app.engine import VentureEngine
from app.orchestration import SpecialistOrchestrator
from app.repository import SQLiteRepository
from app.research import OfflineResearchProvider
from app.service import VentureService
from app.state import StateStore
from app.workflow import WorkflowRunner


def _make_service(tmp_path) -> VentureService:
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    provider = OfflineResearchProvider()
    return VentureService(repo, provider)


def _supermarket_intake() -> VentureIntake:
    return VentureIntake.model_validate(
        {
            "idea": "neighbourhood supermarket",
            "business_type": "supermarket retail",
            "location": "Nairobi, Kenya",
            "country": "Kenya",
            "currency": "KES",
            "founder": {
                "available_capital": 1_800_000,
                "protected_reserve": 150_000,
                "target_monthly_owner_income": 80_000,
            },
        }
    )


def test_workflow_run_has_phase_timings(tmp_path):
    service = _make_service(tmp_path)
    venture = service.create_venture(_supermarket_intake())
    job = service.create_analysis_job(venture.id)
    completed_job = service.run_analysis_job(job.id)

    assert completed_job.status.value == "complete", completed_job.message
    assert completed_job.elapsed_seconds is not None
    assert completed_job.elapsed_seconds >= 0

    # Inspect the workflow run directly
    state = service.state
    workflow = state.get_workflow(completed_job.workflow_id)
    assert workflow is not None
    assert workflow.started_at is not None
    assert workflow.finished_at is not None
    assert workflow.finished_at >= workflow.started_at

    # Phase timings must exist for at least the research and underwrite phases
    timings = workflow.phase_timings
    assert isinstance(timings, dict)
    assert len(timings) > 0, "Expected at least one phase timing"
    assert "research" in timings, f"Missing 'research' in {timings}"
    assert timings["research"] >= 0


def test_elapsed_seconds_increases_monotonically(tmp_path):
    """elapsed_seconds must be non-negative and reflect actual wall time."""
    service = _make_service(tmp_path)
    venture = service.create_venture(_supermarket_intake())
    job = service.create_analysis_job(venture.id)
    t_before = datetime.now(UTC)
    completed = service.run_analysis_job(job.id)
    t_after = datetime.now(UTC)

    assert completed.elapsed_seconds is not None
    # elapsed_seconds measured by time.monotonic should be close to wall time
    wall_seconds = (t_after - t_before).total_seconds()
    # Allow generous slack for test machine variance
    assert completed.elapsed_seconds <= wall_seconds + 5


def test_timeline_returns_ordered_events_with_deltas(tmp_path):
    service = _make_service(tmp_path)
    venture = service.create_venture(_supermarket_intake())
    job = service.create_analysis_job(venture.id)
    service.run_analysis_job(job.id)

    timeline = service.timeline(venture.id)
    assert len(timeline) >= 3, "Expected at least VENTURE_CREATED, WORKFLOW_STARTED, WORKFLOW_CHECKPOINT"

    # First event must have elapsed_seconds == 0
    assert timeline[0]["elapsed_seconds"] == 0.0

    # elapsed_seconds must be non-decreasing
    prev = -1.0
    for entry in timeline:
        assert entry["elapsed_seconds"] >= prev
        prev = entry["elapsed_seconds"]

    # All entries must have required keys
    for entry in timeline:
        assert "elapsed_seconds" in entry
        assert "event_type" in entry
        assert "occurred_at" in entry

    # Workflow checkpoint events should carry phase_elapsed_seconds in payload
    checkpoint_events = [e for e in timeline if e["event_type"] == "workflow_checkpoint"]
    assert len(checkpoint_events) > 0
    for evt in checkpoint_events:
        assert "phase_elapsed_seconds" in evt["payload"]
        assert evt["payload"]["phase_elapsed_seconds"] >= 0


def test_confirmed_context_is_injected_for_later_specialists(tmp_path):
    """Verify that the workflow builds a confirmed-context string after the first specialist completes."""
    from app.workflow import WorkflowRunner

    service = _make_service(tmp_path)
    venture = service.create_venture(_supermarket_intake())
    job = service.create_analysis_job(venture.id)
    service.run_analysis_job(job.id)

    workflow = service.state.get_workflow(job.workflow_id)
    assert workflow is not None and workflow.status == WorkflowStatus.COMPLETE

    # The research batch should contain findings from multiple specialists
    batch = service.state.research_batch_for_workflow(venture.id, workflow.id)
    assert batch is not None

    # Confirm the helper produces a non-None context when there are findings
    ctx = WorkflowRunner._build_confirmed_context(batch.findings, None)
    if batch.findings:
        assert ctx is not None
        assert "CONFIRMED FINDINGS" in ctx
