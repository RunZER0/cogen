import json

import pytest

from app.domain import Confidence, EvidenceType, SpecialistRole, Venture, VentureIntake
from app.engine import VentureEngine
from app.evidence import EvidenceLedger
from app.research import OpenRouterGroundedResearchProvider, ResearchFinding


def _venture() -> Venture:
    intake = VentureIntake.model_validate(
        {
            "idea": "Open a specialty coffee shop",
            "business_type": "coffee shop",
            "location": "Austin, Texas, United States",
            "locality": "Austin",
            "subdivision": "Texas",
            "country": "United States",
            "currency": "USD",
            "launch_target_months": 6,
            "founder": {
                "available_capital": 85_000,
                "protected_reserve": 15_000,
                "debt_available": 0,
                "target_monthly_owner_income": 7_500,
                "max_acceptable_loss": 30_000,
                "time_commitment": "full-time",
                "experience": "first-time cafe operator",
            },
        }
    )
    return VentureEngine().initialise(Venture(intake=intake))


def test_openrouter_uses_tavily_context_and_fixed_model(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class Response:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        if url.endswith("/search"):
            return Response(
                {
                    "results": [
                        {
                            "title": "Austin Public Health",
                            "url": "https://www.austintexas.gov/health/programs/fixed-food-establishments",
                            "content": "Fixed food establishments need a permit and inspection.",
                        }
                    ]
                }
            )
        return Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "findings": [
                                        {
                                            "assumption_key": "regulatory_registration_path",
                                            "claim": "Austin requires a food establishment permit.",
                                            "value": None,
                                            "unit": None,
                                            "evidence_type": "official",
                                            "confidence": "verified",
                                            "source_title": "Austin Public Health",
                                            "source_url": "https://www.austintexas.gov/health/programs/fixed-food-establishments",
                                            "notes": None,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("httpx.post", fake_post)
    provider = OpenRouterGroundedResearchProvider(
        model="google/gemini-3.5-flash-lite",
        api_key="test-openrouter",
        tavily_api_key="test-tavily",
    )

    findings = provider.research(
        _venture(),
        role=SpecialistRole.REGULATORY,
        mandate="Find official food permit requirements.",
    )

    search_url, _ = calls[0]
    completion_url, request = calls[1]
    assert search_url == "https://api.tavily.com/search"
    assert completion_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request["model"] == "google/gemini-3.5-flash-lite"
    assert "tools" not in request
    assert findings[0].evidence_type == EvidenceType.OFFICIAL
    assert findings[0].confidence == Confidence.VERIFIED


def test_regulatory_claim_from_non_authority_url_is_rejected() -> None:
    finding = ResearchFinding(
        assumption_key="regulatory_registration_path",
        claim="A consultant says a permit is mandatory.",
        value=None,
        unit=None,
        evidence_type=EvidenceType.OFFICIAL,
        confidence=Confidence.VERIFIED,
        source_title="Consultant guide",
        source_url="https://example-consultant.com/austin-permits",
        role=SpecialistRole.REGULATORY,
    )

    prepared = EvidenceLedger().prepare(
        _venture(), [finding], role=SpecialistRole.REGULATORY
    )

    assert prepared.accepted == []
    assert prepared.rejected == [
        "regulatory_registration_path: regulatory claim lacks a recognizable official authority URL"
    ]


def test_openrouter_error_is_not_silently_treated_as_empty_evidence(monkeypatch) -> None:
    class Response:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    def fake_post(url, **kwargs):
        if url.endswith("/search"):
            return Response(
                {
                    "results": [
                        {
                            "url": "https://example.com/source",
                            "content": "Source excerpt.",
                        }
                    ]
                }
            )
        return Response({"error": {"code": 502, "message": "Provider returned an empty response"}})

    monkeypatch.setattr("httpx.post", fake_post)
    provider = OpenRouterGroundedResearchProvider(
        api_key="test-openrouter", tavily_api_key="test-tavily"
    )

    with pytest.raises(RuntimeError, match="OpenRouter research error 502"):
        provider.research(_venture(), role=SpecialistRole.MARKET, mandate="Find demand evidence.")
