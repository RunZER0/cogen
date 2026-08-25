from __future__ import annotations

from dataclasses import dataclass

from app.domain import (
    AssumptionCategory,
    Confidence,
    ContradictionRecord,
    Evidence,
    EvidenceType,
    SpecialistRole,
    Venture,
)
from app.research import ResearchFinding
from app.source_router import policy_for, source_strength


DEPENDENCIES: dict[str, set[str]] = {
    "competition_local": {"transactions_per_day", "average_basket"},
    "transactions_per_day": set(),
    "average_basket": set(),
    "monthly_rent": set(),
    "gross_margin_pct": set(),
    "setup_costs": set(),
}


@dataclass(slots=True)
class EvidencePreparation:
    accepted: list[ResearchFinding]
    contradictions: list[ContradictionRecord]
    rejected: list[str]


class EvidenceLedger:
    """Deterministic guardrail around model-generated research."""

    def prepare(
        self,
        venture: Venture,
        findings: list[ResearchFinding],
        *,
        role: SpecialistRole | None = None,
    ) -> EvidencePreparation:
        fingerprints = {item.fingerprint for item in venture.evidence if item.fingerprint}
        accepted: list[ResearchFinding] = []
        contradictions: list[ContradictionRecord] = []
        rejected: list[str] = []
        policy = policy_for(role) if role else None

        for finding in findings:
            if not finding.assumption_key or not finding.claim:
                rejected.append("missing assumption_key or claim")
                continue
            if policy and policy.official_required:
                if finding.evidence_type != EvidenceType.OFFICIAL or not finding.source_url:
                    rejected.append(
                        f"{finding.assumption_key}: regulatory claim lacks an official source"
                    )
                    continue
            if (
                finding.evidence_type in {EvidenceType.MODEL, EvidenceType.DEMO}
                and finding.confidence in {Confidence.HIGH, Confidence.VERIFIED}
            ):
                finding.confidence = Confidence.LOW
            candidate = self._as_evidence(finding, role)
            if candidate.fingerprint in fingerprints:
                continue
            fingerprints.add(candidate.fingerprint)
            contradictions.extend(self._contradictions(venture, candidate))
            accepted.append(finding)
        return EvidencePreparation(accepted, contradictions, rejected)

    @staticmethod
    def _as_evidence(finding: ResearchFinding, role: SpecialistRole | None) -> Evidence:
        return Evidence(
            assumption_key=finding.assumption_key,
            claim=finding.claim,
            value=finding.value,
            unit=finding.unit,
            evidence_type=finding.evidence_type,
            confidence=finding.confidence,
            source_title=finding.source_title,
            source_url=finding.source_url,
            notes=finding.notes,
            role=role,
            materiality=source_strength(finding.evidence_type) * 10,
        )

    @staticmethod
    def _contradictions(venture: Venture, candidate: Evidence) -> list[ContradictionRecord]:
        if not candidate.assumption_key:
            return []
        output: list[ContradictionRecord] = []
        for existing in venture.evidence:
            if existing.stale or existing.assumption_key != candidate.assumption_key:
                continue
            if not isinstance(existing.value, (int, float)) or not isinstance(
                candidate.value, (int, float)
            ):
                continue
            denominator = max(abs(float(existing.value)), abs(float(candidate.value)), 1.0)
            delta = abs(float(existing.value) - float(candidate.value)) / denominator
            if delta < 0.10:
                continue
            output.append(
                ContradictionRecord(
                    venture_id=venture.id,
                    assumption_key=candidate.assumption_key,
                    evidence_id_a=existing.id,
                    description=(
                        f"Conflicting values for {candidate.assumption_key}: "
                        f"{existing.value} from {existing.source_title} versus "
                        f"{candidate.value} from {candidate.source_title}."
                    ),
                    materiality=min(10.0, 1.0 + delta * 5),
                )
            )
        return output


def invalidate_dependents(venture: Venture, changed_key: str) -> list[str]:
    invalidated: list[str] = []
    queue = [changed_key]
    seen: set[str] = set()
    assumptions = venture.assumption_map()
    while queue:
        upstream = queue.pop(0)
        if upstream in seen:
            continue
        seen.add(upstream)
        for downstream in DEPENDENCIES.get(upstream, set()):
            assumption = assumptions.get(downstream)
            if assumption is None:
                continue
            assumption.stale = True
            if assumption.confidence in {Confidence.HIGH, Confidence.VERIFIED}:
                assumption.confidence = Confidence.LOW
            invalidated.append(downstream)
            queue.append(downstream)
    return sorted(set(invalidated))


def _invalidate_categories(
    venture: Venture,
    categories: set[AssumptionCategory],
    reason: str,
) -> list[str]:
    keys = {item.key for item in venture.assumptions if item.category in categories}
    for assumption in venture.assumptions:
        if assumption.key not in keys:
            continue
        assumption.value = None
        assumption.confidence = Confidence.UNKNOWN
        assumption.evidence_ids = []
        assumption.source_note = reason
        assumption.stale = True
    venture.evidence = [item for item in venture.evidence if item.assumption_key not in keys]
    return sorted(keys)


def invalidate_for_location_fork(venture: Venture) -> list[str]:
    """Invalidate facts that cannot safely travel to a new premises/city."""
    return _invalidate_categories(
        venture,
        {
            AssumptionCategory.LOCATION,
            AssumptionCategory.DEMAND,
            AssumptionCategory.COMPETITION,
            AssumptionCategory.REGULATORY,
        },
        "Invalidated by location fork",
    )


def invalidate_for_jurisdiction_fork(venture: Venture) -> list[str]:
    """Invalidate market, legal and execution evidence when the governing jurisdiction changes."""
    return _invalidate_categories(
        venture,
        {
            AssumptionCategory.LOCATION,
            AssumptionCategory.DEMAND,
            AssumptionCategory.COMPETITION,
            AssumptionCategory.REGULATORY,
            AssumptionCategory.EXECUTION,
            AssumptionCategory.COST,
            AssumptionCategory.MARGIN,
            AssumptionCategory.OPERATIONS,
        },
        "Invalidated by jurisdiction fork",
    )
