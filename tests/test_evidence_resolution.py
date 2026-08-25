from app.domain import AddEvidenceRequest, Confidence, EvidenceType, VentureIntake


def us_intake():
    return VentureIntake.model_validate(
        {
            "idea": "Open a specialty coffee shop",
            "business_type": "specialty coffee shop",
            "location": "Austin, Texas, USA",
            "jurisdiction": {
                "country_code": "US",
                "country_name": "United States",
                "subdivision": "Texas",
                "locality": "Austin",
                "currency_code": "USD",
                "locale": "en-US",
                "regulatory_scope": ["United States", "Texas", "Austin"],
            },
            "founder": {
                "available_capital": 180_000,
                "protected_reserve": 30_000,
                "target_monthly_owner_income": 8_000,
            },
        }
    )


def test_weaker_later_founder_guess_cannot_overwrite_stronger_quote(service):
    venture = service.create_venture(us_intake())
    venture = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="monthly_rent",
            claim="Signed landlord quote",
            value=7_250,
            unit="USD/month",
            evidence_type=EvidenceType.QUOTE,
            confidence=Confidence.HIGH,
            source_title="Landlord quote",
            source_url="https://example.com/austin-rent-quote",
        ),
    )
    assert venture.assumption_map()["monthly_rent"].value == 7_250

    venture = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="monthly_rent",
            claim="Founder now guesses rent may be cheaper",
            value=5_000,
            unit="USD/month",
            evidence_type=EvidenceType.FOUNDER,
            confidence=Confidence.LOW,
            source_title="Founder estimate",
        ),
    )
    assert len([item for item in venture.evidence if item.assumption_key == "monthly_rent"]) == 2
    assert venture.assumption_map()["monthly_rent"].value == 7_250
    assert venture.assumption_map()["monthly_rent"].confidence == Confidence.HIGH
    assert venture.assumption_map()["monthly_rent"].source_note == "Signed landlord quote"


def test_stronger_quote_can_replace_low_confidence_founder_prior(service):
    venture = service.create_venture(us_intake())
    venture = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="average_basket",
            claim="Founder estimate",
            value=10.50,
            unit="USD/transaction",
            evidence_type=EvidenceType.FOUNDER,
            confidence=Confidence.LOW,
            source_title="Founder",
        ),
    )
    assert venture.assumption_map()["average_basket"].value == 10.50

    venture = service.add_evidence(
        venture.id,
        AddEvidenceRequest(
            assumption_key="average_basket",
            claim="Observed POS sample",
            value=12.25,
            unit="USD/transaction",
            evidence_type=EvidenceType.OBSERVED,
            confidence=Confidence.HIGH,
            source_title="POS sample",
        ),
    )
    assert venture.assumption_map()["average_basket"].value == 12.25
    assert venture.assumption_map()["average_basket"].source_note == "Observed POS sample"
