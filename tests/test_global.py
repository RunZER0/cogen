import pytest

from app.context import WorkingContextBuilder
from app.domain import (
    AddEvidenceRequest,
    Confidence,
    EvidenceType,
    ForkVentureRequest,
    JurisdictionContext,
    SpecialistRole,
    VentureIntake,
)


GLOBAL_CASES = [
    (
        "Austin, Texas, USA",
        {"country_code": "US", "country_name": "United States", "subdivision": "Texas", "locality": "Austin", "currency_code": "USD", "locale": "en-US", "regulatory_scope": ["United States", "Texas", "Austin"]},
        "USD",
    ),
    (
        "Berlin, Germany",
        {"country_code": "DE", "country_name": "Germany", "subdivision": "Berlin", "locality": "Berlin", "currency_code": "EUR", "locale": "de-DE", "regulatory_scope": ["European Union", "Germany", "Berlin"]},
        "EUR",
    ),
    (
        "Melbourne, Victoria, Australia",
        {"country_code": "AU", "country_name": "Australia", "subdivision": "Victoria", "locality": "Melbourne", "currency_code": "AUD", "locale": "en-AU", "regulatory_scope": ["Australia", "Victoria", "Melbourne"]},
        "AUD",
    ),
    (
        "Shanghai, China",
        {"country_code": "CN", "country_name": "China", "subdivision": "Shanghai", "locality": "Shanghai", "currency_code": "CNY", "locale": "zh-CN", "regulatory_scope": ["China", "Shanghai"]},
        "CNY",
    ),
]


def intake(location, jurisdiction, currency):
    return VentureIntake.model_validate(
        {
            "idea": "Open a specialty coffee shop",
            "business_type": "specialty coffee shop",
            "location": location,
            "jurisdiction": jurisdiction,
            "launch_target_months": 6,
            "founder": {
                "available_capital": 180_000,
                "protected_reserve": 30_000,
                "debt_available": 0,
                "target_monthly_owner_income": 8_000,
                "max_acceptable_loss": 60_000,
                "time_commitment": "full-time",
                "experience": "first-time hospitality founder",
            },
        }
    )


@pytest.mark.parametrize("location,jurisdiction,currency", GLOBAL_CASES)
def test_starter_model_uses_venture_currency_not_developer_country(service, location, jurisdiction, currency):
    venture = service.create_venture(intake(location, jurisdiction, currency))
    assumptions = venture.assumption_map()
    assert assumptions["setup_costs"].unit == currency
    assert assumptions["monthly_rent"].unit == f"{currency}/month"
    assert assumptions["monthly_payroll"].unit == f"{currency}/month"
    assert assumptions["monthly_utilities"].unit == f"{currency}/month"
    assert assumptions["average_basket"].unit == f"{currency}/transaction"
    roadmap_text = " ".join(
        f"{step.title} {step.description} {step.research_query or ''}" for step in venture.roadmap
    )
    assert "KES" not in roadmap_text
    assert "county and sector" not in roadmap_text.lower()


def test_working_context_carries_exact_jurisdiction_and_currency(service):
    location, jurisdiction, _ = GLOBAL_CASES[0]
    venture = service.create_venture(intake(location, jurisdiction, "USD"))
    context = WorkingContextBuilder().build(venture, SpecialistRole.REGULATORY, "resolve permits")
    assert "country_code: US" in context
    assert "subdivision: Texas" in context
    assert "locality: Austin" in context
    assert "currency_code: USD" in context
    assert "United States" in context and "Texas" in context and "Austin" in context
    assert "KES" not in context


def test_unresolved_country_and_currency_block_confident_underwriting(service):
    venture = service.create_venture(
        VentureIntake.model_validate(
            {
                "idea": "Open a specialty coffee shop",
                "business_type": "specialty coffee shop",
                "location": "Springfield",
                "founder": {
                    "available_capital": 180_000,
                    "protected_reserve": 30_000,
                    "target_monthly_owner_income": 8_000,
                },
            }
        )
    )
    result = service.engine.underwrite(venture).underwriting
    assert result is not None
    assert "Country / legal jurisdiction" in result.critical_unknowns
    assert "Operating currency" in result.critical_unknowns
    assert result.decision.value == "needs_data"


def test_verified_qualitative_regulatory_evidence_can_resolve_non_numeric_assumption(service):
    location, jurisdiction, _ = GLOBAL_CASES[0]
    venture = service.create_venture(intake(location, jurisdiction, "USD"))
    venture = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="regulatory_registration_path",
            claim="Official registration path established for the exact jurisdiction",
            evidence_type=EvidenceType.OFFICIAL,
            confidence=Confidence.VERIFIED,
            source_title="Official authority",
            source_url="https://www.usa.gov/start-business",
        ),
    )
    assumption = venture.assumption_map()["regulatory_registration_path"]
    assert assumption.value is None
    assert assumption.evidence_ids
    assert assumption.confidence == Confidence.VERIFIED
    assert "Registration and legal operating path" not in venture.underwriting.critical_unknowns


def test_cross_country_fork_drops_old_market_regulatory_and_execution_truth(service):
    us_location, us_jurisdiction, _ = GLOBAL_CASES[0]
    parent = service.create_venture(intake(us_location, us_jurisdiction, "USD"))
    parent = service.add_evidence(
        parent.id,
        AddEvidenceRequest(
            assumption_key="regulatory_registration_path",
            claim="US registration path",
            evidence_type=EvidenceType.OFFICIAL,
            confidence=Confidence.VERIFIED,
            source_title="US authority",
            source_url="https://www.usa.gov/start-business",
        ),
    )

    child = service.fork(
        parent.id,
        ForkVentureRequest(
            label="Toronto configuration",
            reason="Compare a Canadian market rather than importing US assumptions",
            location="Toronto, Ontario, Canada",
            jurisdiction=JurisdictionContext(
                country_code="CA",
                country_name="Canada",
                subdivision="Ontario",
                locality="Toronto",
                currency_code="CAD",
                locale="en-CA",
                regulatory_scope=["Canada", "Ontario", "Toronto"],
            ),
        ),
    )
    assumptions = child.assumption_map()
    assert child.intake.jurisdiction.country_code == "CA"
    assert assumptions["monthly_rent"].unit == "CAD/month"
    assert assumptions["average_basket"].unit == "CAD/transaction"
    assert assumptions["regulatory_registration_path"].evidence_ids == []
    assert assumptions["regulatory_registration_path"].confidence == Confidence.UNKNOWN
    assert all("usa.gov" not in str(item.source_url or "") for item in child.evidence)
    fork = service.forks(parent.id)[-1]
    assert "regulatory_registration_path" in fork.invalidated_assumptions
    assert "competition_local" in fork.invalidated_assumptions
