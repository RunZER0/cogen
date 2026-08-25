from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.domain import Confidence, EvidenceType, SpecialistRole, Venture
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


class OfflineResearchProvider(ResearchProvider):
    """Deterministic fixture provider for tests and no-key demos.

    These values are deliberately marked DEMO and must never be represented as current market facts.
    """

    RETAIL_FIXTURE = [
        ("setup_costs", 1_300_000.0, "KES", "Illustrative opening setup + stock envelope"),
        ("monthly_rent", 55_000.0, "KES/month", "Illustrative premises rent"),
        ("monthly_payroll", 90_000.0, "KES/month", "Illustrative staffing cost"),
        ("monthly_utilities", 35_000.0, "KES/month", "Illustrative utilities + operating overhead"),
        ("gross_margin_pct", 0.18, "ratio", "Illustrative blended gross margin"),
        ("average_basket", 600.0, "KES/transaction", "Illustrative average customer basket"),
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
        for key, value, unit, claim in self.RETAIL_FIXTURE:
            if allowed is not None and key not in allowed:
                continue
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
    """Live research provider using Gemini 3.7 Flash and Google Search grounding."""

    def __init__(self, model: str = "gemini-3.7-flash", api_key: str | None = None):
        from google import genai

        self.model = model
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))

    def research(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None = None,
        mandate: str | None = None,
    ) -> list[ResearchFinding]:
        prompt = self._build_prompt(venture, role=role, mandate=mandate)
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
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

    def _build_prompt(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None,
        mandate: str | None,
    ) -> str:
        intake = venture.intake
        keys = [item.key for item in venture.assumptions]
        role_text = role.value if role else "general diligence"
        policy = policy_for(role) if role else None
        source_text = ", ".join(policy.preferred_sources) if policy else "best current sources"
        official_text = (
            "For every material regulatory/legal claim, use an official source URL or return no claim."
            if policy and policy.official_required
            else "Prefer primary/current sources over summaries."
        )
        return f"""
You are the {role_text} specialist inside Cogen, a persistent adversarial venture-underwriting system.
Your job is narrow: {mandate or 'find evidence that materially changes the launch decision'}.
Search the current web using Google Search grounding.

Business idea: {intake.idea}
Business type: {intake.business_type}
Location: {intake.location}
Available capital: {intake.founder.available_capital}
Protected reserve: {intake.founder.protected_reserve}
Owner income target: {intake.founder.target_monthly_owner_income}
Existing assumption keys: {keys}

Source preference: {source_text}.
{official_text}
Attack the business case before supporting it. Do not invent a fee, law, licence, supplier, professional,
price, review, statistic, market size or URL. If a material value cannot be established, either return null
with the uncertainty explained or omit it. Do not convert model inference into observed evidence.

Return ONLY a JSON array. Each object must contain:
assumption_key, claim, value (number or null), unit, evidence_type
(official|quote|listing|review|benchmark|observed|founder|model), confidence
(low|medium|high|verified), source_title, source_url (URL or null), notes.
Use existing assumption keys when applicable. Additional regulatory/execution findings must use keys
prefixed regulatory_ or execution_.
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
            return min(claimed, Confidence.LOW, key=lambda item: list(Confidence).index(item))
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
