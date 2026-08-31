import pytest

from app.context import WorkingContextBuilder
from app.domain import IntakeDraftRequest, SpecialistRole, VentureIntake


def _payload(*, location, locality, subdivision, country, currency, locale):
    return {
        "idea": "Open a specialty neighbourhood retail business",
        "business_type": "retail shop",
        "location": location,
        "locality": locality,
        "subdivision": subdivision,
        "country": country,
        "currency": currency,
        "locale": locale,
        "launch_target_months": 6,
        "founder": {
            "available_capital": 85_000,
            "protected_reserve": 15_000,
            "debt_available": 0,
            "target_monthly_owner_income": 7_500,
            "max_acceptable_loss": 30_000,
            "time_commitment": "full-time",
            "experience": "first-time operator",
        },
    }


@pytest.mark.parametrize(
    ("location", "locality", "subdivision", "country", "currency", "locale"),
    [
        ("Austin, Texas, United States", "Austin", "Texas", "United States", "USD", "en-US"),
        ("Melbourne, Victoria, Australia", "Melbourne", "Victoria", "Australia", "AUD", "en-AU"),
        ("Shenzhen, Guangdong, China", "Shenzhen", "Guangdong", "China", "CNY", "zh-CN"),
    ],
)
def test_financial_units_follow_venture_currency(
    service, location, locality, subdivision, country, currency, locale
):
    intake = VentureIntake.model_validate(
        _payload(
            location=location,
            locality=locality,
            subdivision=subdivision,
            country=country,
            currency=currency,
            locale=locale,
        )
    )
    venture = service.create_venture(intake)
    assumptions = venture.assumption_map()
    assert assumptions["setup_costs"].unit == currency
    assert assumptions["monthly_rent"].unit == f"{currency}/month"
    assert assumptions["average_basket"].unit == f"{currency}/transaction"


def test_us_venture_has_no_kenya_specific_core_language(service):
    intake = VentureIntake.model_validate(
        _payload(
            location="Austin, Texas, United States",
            locality="Austin",
            subdivision="Texas",
            country="United States",
            currency="USD",
            locale="en-US",
        )
    )
    venture = service.create_venture(intake)
    rendered = "\n".join(
        [
            *(f"{item.label} {item.unit or ''}" for item in venture.assumptions),
            *(f"{step.title} {step.description} {step.research_query or ''}" for step in venture.roadmap),
        ]
    ).lower()
    assert "kes" not in rendered
    assert "kenya" not in rendered
    assert "county licence" not in rendered
    assert "county license" not in rendered
    assert "usd/month" in rendered
    assert "state/provincial" in rendered
    assert "local" in rendered


def test_bounded_context_carries_jurisdiction_and_currency(service):
    intake = VentureIntake.model_validate(
        _payload(
            location="Austin, Texas, United States",
            locality="Austin",
            subdivision="Texas",
            country="United States",
            currency="USD",
            locale="en-US",
        )
    )
    venture = service.create_venture(intake)
    context = WorkingContextBuilder().build(venture, SpecialistRole.REGULATORY, "resolve legal path")
    assert "country: United States" in context
    assert "subdivision: Texas" in context
    assert "currency: USD" in context


def test_progressive_intake_requires_jurisdiction_and_currency(service):
    draft = service.plan_intake(
        IntakeDraftRequest(
            idea="Open a coffee shop",
            known={"location": "Springfield"},
        )
    )
    assert "country" in draft.missing_material_fields
    assert "currency" in draft.missing_material_fields


def test_offline_fixture_is_withheld_outside_its_native_currency(service):
    """A KES-scale fixture must not be relabelled as another currency.

    Presenting a KES rent magnitude as "USD 55,000/month" would be a fabricated cross-market claim.
    Outside its native currency the provider returns nothing and the venture stays at needs_data.
    """
    from app.research import OfflineResearchProvider

    intake = VentureIntake.model_validate(
        _payload(
            location="Austin, Texas, United States", locality="Austin", subdivision="Texas",
            country="United States", currency="USD", locale="en-US",
        )
    )
    venture = service.create_venture(intake)
    assert OfflineResearchProvider().research(venture, role=SpecialistRole.FINANCE) == []

    service.run_analysis_job(service.create_analysis_job(venture.id).id)
    settled = service.get_venture(venture.id)
    assert settled.underwriting.decision.value == "needs_data"
    assert not [e for e in settled.evidence if e.evidence_type.value == "demo"]


def test_offline_fixture_still_serves_its_native_currency(service, intake_payload):
    from app.research import OfflineResearchProvider

    venture = service.create_venture(VentureIntake.model_validate(intake_payload))
    findings = OfflineResearchProvider().research(venture, role=SpecialistRole.FINANCE)
    assert findings
    assert all(f.unit is None or "KES" in f.unit or f.unit == "ratio" for f in findings)
