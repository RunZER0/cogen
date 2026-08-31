import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    Confidence,
    EvidenceType,
    SpecialistRole,
    SubagentKind,
    SubagentRun,
    SubagentStatus,
    VentureIntake,
)
from app.orchestration import SpecialistOrchestrator
from app.research import ResearchFinding, ResearchProvider
from app.state import StateStore
from app.subagents import SubagentRegistry
from app.workflow import WorkflowRunner


@pytest.fixture
def registry(service):
    return SubagentRegistry(StateStore(service.repository))


async def _settle(registry: SubagentRegistry) -> None:
    """Let every currently-tracked task run to completion — they're plain async functions with
    no real I/O in these tests, so a snapshot-gather resolves as soon as the loop gets to run them."""
    tasks = list(registry._tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_launch_runs_two_sandbox_subagents_concurrently(registry, service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    started = []
    release = asyncio.Event()

    async def work(label):
        async def inner(emit):
            started.append(label)
            emit("text", text=f"working on {label}")
            await release.wait()  # only proceeds once both are confirmed in flight together
            emit("final", text=f"done with {label}")
            return {"label": label}
        return inner

    run_a = await registry.launch(SubagentKind.SANDBOX, venture.id, work=await work("a"), input_payload={})
    run_b = await registry.launch(SubagentKind.SANDBOX, venture.id, work=await work("b"), input_payload={})

    # Both tasks must have actually started (not merely been scheduled one-after-another) before
    # either is allowed to finish — proves they're genuinely concurrent, not sequential.
    for _ in range(50):
        if len(started) == 2:
            break
        await asyncio.sleep(0)
    assert set(started) == {"a", "b"}

    release.set()
    await _settle(registry)

    a = registry.get_run(run_a.id)
    b = registry.get_run(run_b.id)
    assert a.status == SubagentStatus.SUCCEEDED
    assert b.status == SubagentStatus.SUCCEEDED
    assert a.result_payload == {"label": "a"}
    assert b.result_payload == {"label": "b"}

    events_a = {e.type for e in registry.list_events(venture.id, run_a.id)}
    assert {"text", "final"} <= events_a


@pytest.mark.asyncio
async def test_run_inline_persists_failure_and_reraises(registry, service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))

    def failing_work(emit):
        emit("text", text="about to fail")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        registry.run_inline(
            SubagentKind.SPECIALIST, venture.id,
            work=failing_work, role=SpecialistRole.FINANCE, input_payload={},
        )

    runs = registry.list_runs(venture.id, kind=SubagentKind.SPECIALIST)
    assert len(runs) == 1
    assert runs[0].status == SubagentStatus.FAILED
    assert "boom" in runs[0].error


def test_recover_stale_on_boot_marks_orphaned_runs_crashed(registry, service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    stale = SubagentRun(
        kind=SubagentKind.SANDBOX, venture_id=venture.id, status=SubagentStatus.RUNNING,
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=999),
    )
    fresh = SubagentRun(
        kind=SubagentKind.SANDBOX, venture_id=venture.id, status=SubagentStatus.RUNNING,
        heartbeat_at=datetime.now(UTC),
    )
    registry.state.save_subagent_run(stale)
    registry.state.save_subagent_run(fresh)

    recovered = asyncio.run(registry.recover_stale_on_boot(heartbeat_timeout_s=120))

    assert [r.id for r in recovered] == [stale.id]
    assert registry.get_run(stale.id).status == SubagentStatus.CRASHED
    assert registry.get_run(stale.id).error
    assert registry.get_run(fresh.id).status == SubagentStatus.RUNNING


@pytest.mark.asyncio
async def test_wake_hook_fires_only_for_runs_with_a_parent_session(registry, service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    woken = []
    registry.set_wake_hook(lambda run: woken.append(run.id))

    async def work(emit):
        return {"ok": True}

    with_session = await registry.launch(
        SubagentKind.SANDBOX, venture.id, work=work,
        parent_session_id=f"venture:{venture.id}", input_payload={},
    )
    without_session = await registry.launch(
        SubagentKind.SANDBOX, venture.id, work=work, input_payload={},
    )
    await _settle(registry)

    assert woken == [with_session.id]
    assert without_session.id not in woken


class TwoRoundThenFailProvider(ResearchProvider):
    """Round 1 returns a real finding; round 2 (which run_role only reaches if round 1 left
    material assumptions unresolved) blows up — simulates a crash partway through one specialist's
    research so the test can assert round 1's work already survived it."""

    def __init__(self):
        self.calls = 0

    def research(self, venture, *, role=None, mandate=None, emit=None):
        self.calls += 1
        if self.calls == 1:
            return [
                ResearchFinding(
                    assumption_key="monthly_rent",
                    claim="Round 1 rent finding",
                    value=55_000,
                    unit="KES/month",
                    evidence_type=EvidenceType.OBSERVED,
                    confidence=Confidence.MEDIUM,
                    source_title="Test source",
                    role=role,
                )
            ]
        raise RuntimeError("simulated failure in round 2")


def test_specialist_round_crash_does_not_lose_round_one_findings(registry, service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    provider = TwoRoundThenFailProvider()
    orchestrator = SpecialistOrchestrator(provider, roles=[SpecialistRole.FINANCE], max_rounds=2)
    runner = WorkflowRunner(service.repository, registry.state, orchestrator, registry=registry)
    workflow = runner.start(venture.id, "round-crash-test")

    with pytest.raises(RuntimeError, match="simulated failure in round 2"):
        runner._run_research(workflow, venture, stop_after_specialist=None)

    assert provider.calls == 2  # round 2 really was attempted, not skipped
    batch = registry.state.research_batch_for_workflow(venture.id, workflow.id)
    assert batch is not None
    assert any(f["assumption_key"] == "monthly_rent" for f in batch.findings)

    runs = registry.list_runs(venture.id, kind=SubagentKind.SPECIALIST)
    assert len(runs) == 1
    assert runs[0].status == SubagentStatus.FAILED
    assert runs[0].round_index == 1
    event_types = [e.type for e in registry.list_events(venture.id, runs[0].id)]
    assert event_types.count("round_checkpoint") == 1
