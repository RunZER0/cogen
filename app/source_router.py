from __future__ import annotations

from dataclasses import dataclass

from app.domain import EvidenceType, SpecialistRole


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    role: SpecialistRole
    preferred_sources: tuple[str, ...]
    official_required: bool = False
    reject_unsourced_material_claims: bool = True


POLICIES: dict[SpecialistRole, SourcePolicy] = {
    SpecialistRole.FINANCE: SourcePolicy(
        role=SpecialistRole.FINANCE,
        preferred_sources=("supplier quotes", "property listings", "current price lists", "official fees"),
    ),
    SpecialistRole.MARKET: SourcePolicy(
        role=SpecialistRole.MARKET,
        preferred_sources=("maps/business listings", "reviews", "local directories", "demographic proxies"),
    ),
    SpecialistRole.REGULATORY: SourcePolicy(
        role=SpecialistRole.REGULATORY,
        preferred_sources=("official regulator", "official county", "official registry", "statute or rule"),
        official_required=True,
    ),
    SpecialistRole.EXECUTION: SourcePolicy(
        role=SpecialistRole.EXECUTION,
        preferred_sources=("active supplier sites", "professional directories", "service-provider listings"),
    ),
    SpecialistRole.ADVERSARY: SourcePolicy(
        role=SpecialistRole.ADVERSARY,
        preferred_sources=("strongest contradictory evidence", "current competitors", "failure-inducing costs"),
    ),
}


SOURCE_STRENGTH = {
    EvidenceType.OFFICIAL: 1.0,
    EvidenceType.QUOTE: 0.9,
    EvidenceType.OBSERVED: 0.9,
    EvidenceType.LISTING: 0.7,
    EvidenceType.BENCHMARK: 0.65,
    EvidenceType.FOUNDER: 0.55,
    EvidenceType.REVIEW: 0.45,
    EvidenceType.MODEL: 0.25,
    EvidenceType.DEMO: 0.1,
}


def policy_for(role: SpecialistRole) -> SourcePolicy:
    return POLICIES[role]


def source_strength(evidence_type: EvidenceType) -> float:
    return SOURCE_STRENGTH[evidence_type]
