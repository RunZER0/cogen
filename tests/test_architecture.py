import pytest

from app.context import ContextBudget, WorkingContextBuilder
from app.domain import (
    EvidenceType,
    ForkVentureRequest,
    IntakeDraftRequest,
    SandboxRequest,
    SpecialistRole,
    VentureIntake,
    WorkflowPhase,
    WorkflowStatus,
)
from app.evidence import EvidenceLedger
from app.model_runtime import GeminiModelRouter
from app.orchestration import SpecialistOrchestrator
from app.research import ResearchFinding, ResearchProvider
from app.state import StateStore
from app.workflow import SimulatedCrash, WorkflowRunner


def analyzed(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    job = service.create_analysis_job(venture.id)
    completed = service.run_analysis_job(job.id)
    assert completed.status.value == "complete"
    return service.get_venture(venture.id)


def test_progressive_intake_starts_from_bare_idea(service):
    draft = service.plan_intake(IntakeDraftRequest(idea="I want to open a supermarket"))
    assert draft.idea == "I want to open a supermarket"
    assert draft.next_question
    assert "location" in draft.missing_material_fields


def test_event_writes_are_idempotent(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    store = StateStore(service.repository)
    first = store.event(venture.id, "venture_created", "same-key", {"value": 1})
    second = store.event(venture.id, "venture_created", "same-key", {"value": 2})
    assert first.id == second.id
    assert second.payload == {"value": 1}


def test_workflow_resumes_from_checkpoint(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    runner = WorkflowRunner(service.repository, service.state, service.orchestrator, service.engine)
    workflow = runner.start(venture.id, "resume-test")
    with pytest.raises(SimulatedCrash):
        runner.run(workflow.id, stop_after_phase=WorkflowPhase.RESEARCH)

    checkpoint = service.state.get_workflow(workflow.id)
    assert checkpoint.status == WorkflowStatus.RETRYABLE
    assert WorkflowPhase.PLAN in checkpoint.completed_phases
    assert WorkflowPhase.RESEARCH in checkpoint.completed_phases
    attempt_before = checkpoint.attempt

    completed = runner.run(workflow.id)
    assert completed.status == WorkflowStatus.COMPLETE
    assert completed.attempt == attempt_before + 1
    assert completed.phase == WorkflowPhase.COMPLETE


def test_sandbox_never_mutates_canonical_state(service, intake_payload):
    venture = analyzed(service, intake_payload)
    original_rent = venture.assumption_map()["monthly_rent"].value
    experiment = service.run_sandbox(
        venture.id,
        SandboxRequest(
            name="rent shock",
            shocks={"monthly_rent": 250_000},
            simulation_runs=1000,
        ),
    )
    reloaded = service.get_venture(venture.id)
    assert reloaded.assumption_map()["monthly_rent"].value == original_rent
    assert experiment.shocks["monthly_rent"] == 250_000
    assert "never real-world evidence" in experiment.notes[0]


def test_location_fork_preserves_parent_and_invalidates_local_evidence(service, intake_payload):
    parent = analyzed(service, intake_payload)
    parent_rent = parent.assumption_map()["monthly_rent"].value
    child = service.fork(
        parent.id,
        ForkVentureRequest(
            label="Location B",
            reason="Compare a second catchment",
            location="Juja, Kiambu County, Kenya",
        ),
    )
    assert child.parent_venture_id == parent.id
    assert child.intake.location.startswith("Juja")
    assert child.assumption_map()["monthly_rent"].value is None
    assert service.get_venture(parent.id).assumption_map()["monthly_rent"].value == parent_rent
    forks = service.forks(parent.id)
    assert forks[-1].child_venture_id == child.id
    assert "monthly_rent" in forks[-1].invalidated_assumptions


def test_conflicting_numeric_evidence_is_recorded(service, intake_payload):
    venture = analyzed(service, intake_payload)
    finding = ResearchFinding(
        assumption_key="monthly_rent",
        claim="Second current rent quote",
        value=110_000,
        unit="KES/month",
        evidence_type=EvidenceType.QUOTE,
        confidence="high",
        source_title="Landlord quote B",
        source_url="https://example.com/quote-b",
        role=SpecialistRole.FINANCE,
    )
    prepared = EvidenceLedger().prepare(venture, [finding], role=SpecialistRole.FINANCE)
    assert prepared.contradictions
    assert prepared.contradictions[0].assumption_key == "monthly_rent"


class FabricatingProvider(ResearchProvider):
    def research(self, venture, *, role=None, mandate=None):
        del venture, mandate
        if role != SpecialistRole.REGULATORY:
            return []
        return [
            ResearchFinding(
                assumption_key="regulatory_fake_permit",
                claim="A made-up permit is mandatory",
                value=25_000,
                unit="KES",
                evidence_type=EvidenceType.OFFICIAL,
                confidence="verified",
                source_title="No actual source",
                source_url=None,
                role=role,
            )
        ]


def test_regulatory_specialist_cannot_promote_unsourced_claim(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    orchestrator = SpecialistOrchestrator(
        FabricatingProvider(),
        roles=[SpecialistRole.REGULATORY],
    )
    result = orchestrator.run(venture, "workflow-x")
    assert result.findings == []
    assert result.rejected
    assert result.reports[0].rejected_count >= 1
    assert "0 admissible findings" in result.reports[0].summary


def test_specialist_orchestration_uses_all_required_roles(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    result = service.orchestrator.run(venture, "roles-test")
    assert {report.role for report in result.reports} == set(SpecialistRole)


def test_working_context_is_bounded_and_keeps_material_constraints(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    builder = WorkingContextBuilder(ContextBudget(max_assumptions=4, max_evidence=2, max_chars=1800))
    context = builder.build(venture, SpecialistRole.MARKET, "attack demand")
    assert len(context) <= 1800
    assert "available_capital: 1800000.0" in context
    assert "target_monthly_owner_income: 120000.0" in context
    assert "transactions_per_day" in context
    assert "ROLE: market" in context


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, *, model, contents, config):
        del contents, config
        self.calls.append(model)
        if model == "primary":
            raise ConnectionError("primary unavailable")
        return {"model": model}


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


def test_model_router_retries_primary_then_uses_fallback():
    router = GeminiModelRouter("primary", fallback="fallback", attempts_per_model=2)
    client = FakeClient()
    response = router.generate(client, contents="x", config={})
    assert response == {"model": "fallback"}
    assert client.models.calls == ["primary", "primary", "fallback"]
    health = router.snapshot()
    assert health["last_model_used"] == "fallback"
    assert health["fallbacks_used"] == 1
    assert health["failures"]["primary"] == 2


def test_readiness_checks_real_repository_and_research_runtime(service):
    readiness = service.readiness()
    assert readiness["database"] == "ok"
    assert readiness["research_runtime"]["status"] == "ok"


def test_acceptance_contract_contains_exactly_100_numbered_criteria():
    from app.acceptance import CRITERIA

    assert len(CRITERIA) == 100
    assert [item.id for item in CRITERIA] == list(range(1, 101))
    assert len({item.question for item in CRITERIA}) == 100
    assert all(item.implementation and item.verification for item in CRITERIA)


def test_acceptance_contract_covers_all_ten_flagship_domains():
    from app.acceptance import CRITERIA

    categories = {item.category for item in CRITERIA}
    assert len(categories) == 10
    assert all(sum(item.category == category for item in CRITERIA) == 10 for category in categories)
