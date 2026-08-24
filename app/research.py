from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.domain import Confidence, EvidenceType, Venture


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


class ResearchProvider(ABC):
    @abstractmethod
    def research(self, venture: Venture) -> list[ResearchFinding]: ...


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

    def research(self, venture: Venture) -> list[ResearchFinding]:
        is_retail = any(
            token in venture.intake.business_type.lower() or token in venture.intake.idea.lower()
            for token in ("supermarket", "minimart", "retail", "shop", "grocery")
        )
        if not is_retail:
            return []

        findings: list[ResearchFinding] = []
        for key, value, unit, claim in self.RETAIL_FIXTURE:
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
                )
            )
        return findings


class GeminiGroundedResearchProvider(ResearchProvider):
    """Live research provider using Gemini + Google Search grounding."""

    def __init__(self, model: str = "gemini-3.7-flash", api_key: str | None = None):
        from google import genai

        self._genai = genai
        self.model = model
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))

    def research(self, venture: Venture) -> list[ResearchFinding]:
        prompt = self._build_prompt(venture)
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
                )
            )
        return [f for f in findings if f.assumption_key and f.claim]

    def _build_prompt(self, venture: Venture) -> str:
        intake = venture.intake
        keys = [item.key for item in venture.assumptions]
        return f"""
You are performing adversarial pre-launch diligence for a real founder. Search the current web.
Business idea: {intake.idea}
Business type: {intake.business_type}
Location: {intake.location}
Available capital: {intake.founder.available_capital}
Protected reserve: {intake.founder.protected_reserve}
Owner income target: {intake.founder.target_monthly_owner_income}
Existing assumption keys: {keys}

Find evidence that could DISPROVE the business case before evidence that supports it. Research current
costs, local competition, demand proxies, margins where defensibly ascertainable, registration and tax
path, local/county permits, sector regulation, suppliers/service providers and execution dependencies.
Never invent a fee, law, licence, supplier, professional, price or URL. If a value cannot be established,
return null and explain the unknown.

Return ONLY a JSON array. Each object must contain:
assumption_key, claim, value (number or null), unit, evidence_type (official|quote|listing|review|benchmark|observed|founder|model),
confidence (low|medium|high|verified), source_title, source_url (URL or null), notes.
Use the existing assumption keys when applicable. For additional regulatory/execution findings use keys
prefixed regulatory_ or execution_. Prefer official sources for registration, tax and legal requirements.
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
        if source_url and grounded_urls and source_url not in grounded_urls:
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
