from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.context import WorkingContextBuilder
from app.domain import Confidence, EvidenceType, SpecialistRole, Venture
from app.model_runtime import GeminiModelRouter
from app.source_router import policy_for


@dataclass(slots=True)
class ResearchFinding:
    assumption_key: str
    claim: str
    value: float | None
    unit: str | None
    evidence_type: EvidenceType
    confidence: Confidence
    source_title: str
    source_url: str | None = None
    notes: str | None = None
    role: SpecialistRole | None = None


class ResearchProvider(ABC):
    @abstractmethod
    def research(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None = None,
        mandate: str | None = None,
    ) -> list[ResearchFinding]: ...

    def runtime_health(self) -> dict[str, object]:
        return {"provider": type(self).__name__, "status": "ok"}


class OfflineResearchProvider(ResearchProvider):
    """Deterministic fixture provider for tests and no-key demos.

    Values are deliberately marked DEMO and must never be represented as current market facts. Monetary
    units are rendered in the Venture Twin's currency so the fixture cannot leak a country assumption.
    """

    RETAIL_FIXTURE = [
        ("setup_costs", 1_300_000.0, "currency", "Illustrative opening setup + stock envelope"),
        ("monthly_rent", 55_000.0, "currency/month", "Illustrative premises rent"),
        ("monthly_payroll", 90_000.0, "currency/month", "Illustrative staffing cost"),
        ("monthly_utilities", 35_000.0, "currency/month", "Illustrative utilities + operating overhead"),
        ("gross_margin_pct", 0.18, "ratio", "Illustrative blended gross margin"),
        ("average_basket", 600.0, "currency/transaction", "Illustrative average customer basket"),
        ("transactions_per_day", 120.0, "transactions/day", "UNVERIFIED demand/footfall assumption"),
        ("days_open_month", 30.0, "days/month", "Illustrative trading days"),
        ("shrinkage_pct", 0.02, "ratio", "Illustrative shrinkage/spoilage rate"),
    ]

    ROLE_KEYS = {
        SpecialistRole.FINANCE: {
            "setup_costs",
            "monthly_rent",
            "monthly_payroll",
            "monthly_utilities",
            "gross_margin_pct",
            "shrinkage_pct",
        },
        SpecialistRole.MARKET: {"average_basket", "transactions_per_day", "days_open_month"},
        SpecialistRole.REGULATORY: set(),
        SpecialistRole.EXECUTION: set(),
        SpecialistRole.ADVERSARY: {"transactions_per_day", "gross_margin_pct", "monthly_rent"},
    }

    def research(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None = None,
        mandate: str | None = None,
    ) -> list[ResearchFinding]:
        del mandate
        is_retail = any(
            token in venture.intake.business_type.lower() or token in venture.intake.idea.lower()
            for token in ("supermarket", "minimart", "retail", "shop", "grocery")
        )
        if not is_retail:
            return []

        allowed = self.ROLE_KEYS.get(role) if role else None
        findings: list[ResearchFinding] = []
        currency = venture.intake.monetary_unit
        for key, value, unit_template, claim in self.RETAIL_FIXTURE:
            if allowed is not None and key not in allowed:
                continue
            unit = unit_template.replace("currency", currency)
            confidence = Confidence.LOW if key == "transactions_per_day" else Confidence.MEDIUM
            findings.append(
                ResearchFinding(
                    assumption_key=key,
                    claim=claim,
                    value=value,
                    unit=unit,
                    evidence_type=EvidenceType.DEMO,
                    confidence=confidence,
                    source_title="Demo fixture — replace with live grounded research",
                    notes="Deterministic local fixture; not a factual statement about the user's market.",
                    role=role,
                )
            )
        return findings


class GeminiGroundedResearchProvider(ResearchProvider):
    """Live research provider using bounded context, Google Search grounding and model fallback."""

    def __init__(
        self,
        model: str = "gemini-3.7-flash",
        api_key: str | None = None,
        *,
        fallback_model: str | None = "gemini-3.6-flash",
        attempts_per_model: int = 2,
        context_builder: WorkingContextBuilder | None = None,
    ):
        from google import genai

        self.model = model
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
        self.router = GeminiModelRouter(
            model,
            fallback=fallback_model,
            attempts_per_model=attempts_per_model,
        )
        self.context_builder = context_builder or WorkingContextBuilder()

    def research(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None = None,
        mandate: str | None = None,
    ) -> list[ResearchFinding]:
        prompt = self._build_prompt(venture, role=role, mandate=mandate)
        from google.genai import types

        response = self.router.generate(
            self.client,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
            ),
        )
        payload = json.loads(response.text or "[]")
        if not isinstance(payload, list):
            raise ValueError("Grounded research response must be a JSON list")

        grounded_urls = self._grounded_urls(response)
        findings: list[ResearchFinding] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            source_url = raw.get("source_url")
            confidence = self._confidence(raw.get("confidence"), source_url, grounded_urls)
            findings.append(
                ResearchFinding(
                    assumption_key=str(raw.get("assumption_key", "")).strip(),
                    claim=str(raw.get("claim", "")).strip(),
                    value=self._float_or_none(raw.get("value")),
                    unit=raw.get("unit"),
                    evidence_type=self._evidence_type(raw.get("evidence_type")),
                    confidence=confidence,
                    source_title=str(raw.get("source_title") or "Gemini grounded research"),
                    source_url=source_url,
                    notes=raw.get("notes"),
                    role=role,
                )
            )
        return [finding for finding in findings if finding.assumption_key and finding.claim]

    def runtime_health(self) -> dict[str, object]:
        return {
            "provider": type(self).__name__,
            "status": "ok",
            "model": self.router.snapshot(),
        }

    def _build_prompt(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None,
        mandate: str | None,
    ) -> str:
        role = role or SpecialistRole.ADVERSARY
        mandate = mandate or "find evidence that materially changes the launch decision"
        policy = policy_for(role)
        source_text = ", ".join(policy.preferred_sources)
        official_text = (
            "For every material regulatory/legal claim, use a current primary official source URL from the authority that actually governs this venture or return no claim."
            if policy.official_required
            else "Prefer primary/current sources over summaries."
        )
        working_context = self.context_builder.build(venture, role, mandate)
        return f"""
You are a scoped research specialist inside Cogen, a persistent adversarial venture-underwriting system.
You do not own venture state. You return candidate evidence; deterministic evidence policy decides what
is admitted. Search the current web using Google Search grounding.

{working_context}

SOURCE POLICY: {source_text}.
{official_text}
Respect the venture's explicit country, subdivision/locality and currency. Never import laws, taxes, licences,
fees, prices, wage norms or monetary units from another jurisdiction merely because they are easier to find.
When the governing level is unclear, identify that uncertainty instead of guessing.

Attack the business case before supporting it. Do not invent a fee, law, licence, supplier, professional,
price, review, statistic, market size or URL. If a material value cannot be established, either return null
with the uncertainty explained or omit it. Do not convert model inference or prior simulation into observed
evidence. Prefer evidence capable of resolving a critical or high-impact unknown over low-value background.

Return ONLY a JSON array. Each object must contain:
assumption_key, claim, value (number or null), unit, evidence_type
(official|quote|listing|review|benchmark|observed|founder|model), confidence
(low|medium|high|verified), source_title, source_url (URL or null), notes.
Use existing assumption keys when applicable. Additional regulatory/execution findings must use keys
prefixed regulatory_ or execution_. Monetary units must use the Venture Twin currency code.
""".strip()

    @staticmethod
    def _grounded_urls(response: Any) -> set[str]:
        urls: set[str] = set()
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return urls
        metadata = getattr(candidates[0], "grounding_metadata", None)
        chunks = getattr(metadata, "grounding_chunks", None) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if uri:
                urls.add(str(uri))
        return urls

    @staticmethod
    def _confidence(raw: Any, source_url: str | None, grounded_urls: set[str]) -> Confidence:
        try:
            claimed = Confidence(str(raw).lower())
        except ValueError:
            claimed = Confidence.LOW
        if not source_url:
            return Confidence.LOW
        if grounded_urls and source_url not in grounded_urls:
            return Confidence.LOW
        return claimed

    @staticmethod
    def _evidence_type(raw: Any) -> EvidenceType:
        try:
            return EvidenceType(str(raw).lower())
        except ValueError:
            return EvidenceType.MODEL

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
