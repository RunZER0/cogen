from __future__ import annotations

from dataclasses import dataclass

from app.domain import Confidence, Evidence, SpecialistRole, Venture


CONFIDENCE_RISK = {
    Confidence.UNKNOWN: 1.0,
    Confidence.LOW: 0.8,
    Confidence.MEDIUM: 0.5,
    Confidence.HIGH: 0.2,
    Confidence.VERIFIED: 0.0,
}

ROLE_KEY_HINTS: dict[SpecialistRole, tuple[str, ...]] = {
    SpecialistRole.FINANCE: ("cost", "rent", "payroll", "utilities", "margin", "shrinkage", "capital"),
    SpecialistRole.MARKET: ("basket", "transaction", "demand", "competition", "location", "days_open"),
    SpecialistRole.REGULATORY: ("regulatory", "registration", "tax", "licen", "permit", "inspection"),
    SpecialistRole.EXECUTION: ("execution", "supplier", "premises", "equipment", "staff", "payment"),
    SpecialistRole.ADVERSARY: (),
}


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_assumptions: int = 14
    max_evidence: int = 12
    max_chars: int = 9000


class WorkingContextBuilder:
    """Build a bounded, materiality-first context from durable Venture Twin state."""

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()

    def build(self, venture: Venture, role: SpecialistRole, mandate: str) -> str:
        assumptions = sorted(
            venture.assumptions,
            key=lambda item: self._assumption_score(item, role),
            reverse=True,
        )[: self.budget.max_assumptions]
        chosen_keys = {item.key for item in assumptions}
        evidence = sorted(
            [item for item in venture.evidence if not item.stale],
            key=lambda item: self._evidence_score(item, role, chosen_keys),
            reverse=True,
        )[: self.budget.max_evidence]

        intake = venture.intake
        founder = intake.founder
        lines = [
            f"ROLE: {role.value}",
            f"MANDATE: {mandate}",
            "VENTURE JURISDICTION:",
            f"- location_text: {intake.location}",
            f"- locality: {intake.locality}",
            f"- subdivision: {intake.subdivision}",
            f"- country: {intake.country}",
            f"- currency: {intake.monetary_unit}",
            f"- locale: {intake.locale}",
            "FOUNDER CONSTRAINTS:",
            f"- idea: {intake.idea}",
            f"- business_type: {intake.business_type}",
            f"- available_capital: {founder.available_capital}",
            f"- protected_reserve: {founder.protected_reserve}",
            f"- debt_available: {founder.debt_available}",
            f"- target_monthly_owner_income: {founder.target_monthly_owner_income}",
            f"- max_acceptable_loss: {founder.max_acceptable_loss}",
            f"- launch_target_months: {intake.launch_target_months}",
            "MATERIAL ASSUMPTIONS:",
        ]
        for item in assumptions:
            lines.append(
                f"- {item.key}: value={item.value!r} unit={item.unit!r} "
                f"confidence={item.confidence.value} critical={item.critical} "
                f"impact={item.impact_weight} stale={item.stale}"
            )

        lines.append("ADMITTED EVIDENCE:")
        if not evidence:
            lines.append("- none yet")
        for item in evidence:
            lines.append(self._evidence_line(item))

        if venture.underwriting:
            result = venture.underwriting
            lines.extend(
                [
                    "CURRENT UNDERWRITING:",
                    f"- decision: {result.decision.value}",
                    f"- evidence_coverage: {result.evidence_coverage}",
                    f"- model_confidence: {result.model_confidence.value}",
                    f"- critical_unknowns: {result.critical_unknowns}",
                    f"- biggest_risks: {result.biggest_risks}",
                ]
            )

        context = "\n".join(lines)
        if len(context) <= self.budget.max_chars:
            return context
        return context[: self.budget.max_chars - 120] + "\n[CONTEXT TRUNCATED AT BUDGET; durable state remains available]"

    @staticmethod
    def _assumption_score(item, role: SpecialistRole) -> float:
        hints = ROLE_KEY_HINTS[role]
        role_bonus = 2.0 if any(token in item.key.lower() for token in hints) else 0.0
        critical_bonus = 3.0 if item.critical else 0.0
        stale_bonus = 2.0 if item.stale else 0.0
        return item.impact_weight * (1.0 + CONFIDENCE_RISK[item.confidence]) + role_bonus + critical_bonus + stale_bonus

    @staticmethod
    def _evidence_score(item: Evidence, role: SpecialistRole, chosen_keys: set[str]) -> float:
        key_bonus = 4.0 if item.assumption_key in chosen_keys else 0.0
        role_bonus = 2.0 if item.role == role else 0.0
        confidence_bonus = 1.0 - CONFIDENCE_RISK[item.confidence]
        source_bonus = 1.0 if item.source_url else 0.0
        return item.materiality + key_bonus + role_bonus + confidence_bonus + source_bonus

    @staticmethod
    def _evidence_line(item: Evidence) -> str:
        return (
            f"- {item.assumption_key}: {item.claim} | value={item.value!r} {item.unit or ''} | "
            f"confidence={item.confidence.value} type={item.evidence_type.value} | "
            f"source={item.source_title} {str(item.source_url) if item.source_url else ''}"
        )
