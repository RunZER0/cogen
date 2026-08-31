from app.domain import (
    AddEvidenceRequest,
    ApplyChangeRequest,
    Confidence,
    Decision,
    EvidenceType,
    VentureIntake,
)


def create_and_analyze(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    job = service.create_analysis_job(venture.id)
    completed = service.run_analysis_job(job.id)
    assert completed.status.value == "complete"
    return service.get_venture(venture.id)


def test_new_venture_starts_with_structured_assumptions(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    keys = {item.key for item in venture.assumptions}
    assert "transactions_per_day" in keys
    assert "regulatory_registration_path" in keys
    assert venture.underwriting is None


def test_offline_analysis_is_explicitly_demo_evidence(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    assert venture.evidence
    assert all(item.evidence_type == EvidenceType.DEMO for item in venture.evidence)
    assert all("Demo fixture" in item.source_title for item in venture.evidence)


def test_demand_stays_critical_and_weak_after_fixture(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    demand = venture.assumption_map()["transactions_per_day"]
    assert demand.critical is True
    assert demand.confidence == Confidence.LOW
    assert "Daily transactions / demand" in venture.underwriting.critical_unknowns


def test_decision_is_not_approve_while_critical_unknowns_exist(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    assert venture.underwriting.decision != Decision.APPROVE


def test_needs_data_critical_unknowns_have_no_duplicate_gaps(service, intake_payload):
    """A key that is both a weak critical assumption and a required financial key must be named
    once, not twice under two different label formats (e.g. "Monthly premises rent" from the
    assumption label and "Monthly Rent" from the prettified key) — found live when a fresh
    venture's critical_unknowns listed the same missing rent figure under both spellings."""
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    assert venture.underwriting is None
    venture = service.engine.underwrite(venture)
    unknowns = venture.underwriting.critical_unknowns
    assert unknowns
    normalized = [item.lower().replace(" ", "") for item in unknowns]
    assert len(normalized) == len(set(normalized)), f"duplicate critical_unknowns entries: {unknowns}"
    # Specifically the pair observed live: the assumption label and the pretty-printed key for rent.
    assert not ("Monthly premises rent" in unknowns and "Monthly Rent" in unknowns)
    assert not ("Total setup and opening inventory cost" in unknowns and "Setup Costs" in unknowns)


def test_simulation_is_bounded_and_reproducible(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    first = venture.underwriting.break_even_probability_12m
    venture2 = service.engine.underwrite(venture)
    second = venture2.underwriting.break_even_probability_12m
    assert 0 <= first <= 1
    assert first == second
    assert venture2.underwriting.simulation_runs == 5000


def test_evidence_coverage_increases_when_critical_demand_verified(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    before = venture.underwriting.evidence_coverage
    updated = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="transactions_per_day",
            claim="Founder completed a counted footfall/transaction validation exercise",
            value=145,
            unit="transactions/day",
            evidence_type=EvidenceType.OBSERVED,
            confidence=Confidence.VERIFIED,
            source_title="Founder field observation",
        ),
    )
    assert updated.underwriting.evidence_coverage > before
    assert updated.assumption_map()["transactions_per_day"].value == 145


def test_material_rent_change_recomputes_model(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    before_profit = venture.underwriting.monthly_operating_profit_base
    changed = service.apply_change(
        venture.id,
        ApplyChangeRequest(
            summary="Landlord quote increased",
            assumption_key="monthly_rent",
            new_value=120_000,
            confidence=Confidence.HIGH,
        ),
    )
    assert changed.underwriting.monthly_operating_profit_base < before_profit
    assert changed.changes[-1].old_value == 55_000


def test_capital_shortfall_can_kill_configuration(service, intake_payload):
    payload = intake_payload.copy()
    payload["founder"] = {**intake_payload["founder"], "available_capital": 900_000}
    venture = create_and_analyze(service, payload)
    assert venture.underwriting.decision == Decision.REJECT
    assert venture.status.value == "killed"


def test_irreversible_roadmap_step_locked_with_unknowns(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    location = next(step for step in venture.roadmap if step.phase == "LOCATION")
    assert location.irreversible is True
    assert location.status.value == "locked"


def test_first_roadmap_step_can_complete(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    first = venture.roadmap[0]
    assert first.status.value == "ready"
    updated = service.complete_step(venture.id, first.id)
    assert updated.roadmap[0].status.value == "complete"


def test_locked_step_cannot_be_completed(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    locked = next(step for step in venture.roadmap if step.status.value == "locked")
    try:
        service.complete_step(venture.id, locked.id)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "locked" in str(exc)


def test_unknown_assumption_evidence_is_rejected(service, intake_payload):
    venture = create_and_analyze(service, intake_payload)
    try:
        service.add_evidence(
            venture.id,
            AddEvidenceRequest(
                assumption_key="imaginary_metric",
                claim="No such assumption",
                value=1,
                source_title="Test",
            ),
        )
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_direct_model_evidence_cannot_claim_high_confidence(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    updated = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="monthly_rent",
            claim="A model estimated the rent",
            value=100_000,
            unit="KES/month",
            evidence_type=EvidenceType.MODEL,
            confidence=Confidence.VERIFIED,
            source_title="Model estimate",
            source_url="https://example.invalid/not-evidence",
        ),
    )
    evidence = updated.evidence[-1]
    assert evidence.confidence == Confidence.LOW
    assert updated.assumption_map()["monthly_rent"].confidence == Confidence.LOW


def test_direct_official_evidence_requires_authority_url(service, intake_payload):
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    request = AddEvidenceRequest(
        assumption_key="regulatory_registration_path",
        claim="A permit costs 6,000 KES",
        value=6_000,
        unit="KES",
        evidence_type=EvidenceType.OFFICIAL,
        confidence=Confidence.VERIFIED,
        source_title="A random page",
        source_url="https://example.invalid/permit",
    )
    try:
        service.add_evidence(venture.id, request)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "official" in str(exc).lower()


def test_identify_rescue_candidate_when_assumptions_verified(service, intake_payload):
    from app.domain import Confidence, EvidenceType
    from app.simulation import identify_rescue_candidate
    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    # Make all financial assumptions verified with a thin margin that yields negative monthly profit
    overrides = {
        "gross_margin_pct": (0.05, "5% gross margin"),
        "monthly_rent": (50000.0, "50k rent"),
        "monthly_payroll": (80000.0, "80k payroll"),
        "monthly_utilities": (10000.0, "10k utilities"),
        "average_basket": (1000.0, "1k basket"),
        "transactions_per_day": (10.0, "10 tx/day"),
        "days_open_month": (26.0, "26 days"),
        "shrinkage_pct": (0.01, "1% shrinkage"),
        "setup_costs": (100000.0, "100k setup"),
    }
    for key, (val, claim) in overrides.items():
        venture = service.add_evidence(
            venture.id,
            AddEvidenceRequest(
                assumption_key=key,
                claim=claim,
                value=val,
                evidence_type=EvidenceType.OBSERVED,
                confidence=Confidence.VERIFIED,
                source_title="Test verification",
            ),
        )
    # Baseline monthly operating profit is negative
    assert venture.underwriting.monthly_operating_profit_base < 0
    # identify_rescue_candidate should still find the structural sensitivity lever (e.g. gross margin expansion)
    rescue = identify_rescue_candidate(venture)
    assert rescue is not None
    assert "assumption_key" in rescue
    assert rescue["gain"] > 0
    assert rescue["assumption_key"] == "gross_margin_pct"

