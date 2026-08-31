from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

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



# A handful of national government domains that don't follow any "gov"/"go" pattern at all
# (each is the one canonical host suffix for that country's federal/EU administration).
NAMED_AUTHORITY_SUFFIXES: tuple[str, ...] = (
    "admin.ch",  # Switzerland
    "bund.de",  # Germany (federal)
    "europa.eu",  # European Union institutions
)

# These hosts are useful in tests and examples but are not an authority.  A suffix-only check would
# otherwise admit a fabricated claim from ``fake.gov`` as if it had come from a real government.
PLACEHOLDER_AUTHORITY_HOSTS: frozenset[str] = frozenset({
    "fake.gov",
    "test.gov",
    "example.gov",
    "invalid.gov",
    "placeholder.gov",
})


def is_likely_official_authority_url(source_url: str | None) -> bool:
    """Conservative URL check for regulatory evidence admission.

    This is deliberately a gate, not a claim that every government authority uses one domain pattern.
    When a source cannot be recognized as an authority domain, the claim remains unresolved and requires
    founder validation rather than being promoted as official evidence.

    Two systematic government-domain conventions are recognized, not just the ".gov" one: many
    Anglophone/Commonwealth jurisdictions use ".gov" (gov.uk, gov.au, gov.in, gov.ng, gov.za, ...), while
    a large second group of countries (Japan, South Korea, Kenya, Tanzania, Uganda, Thailand, Indonesia,
    Costa Rica, ...) use ".go.<country-code>" instead. Recognizing only the first convention would silently
    make regulatory evidence inadmissible for an entire class of jurisdictions rather than for a genuine
    source-quality reason, which is a jurisdiction bias, not a safety property.
    """
    if not source_url:
        return False
    host = (urlparse(source_url).hostname or "").lower().strip(".")
    if not host:
        return False
    if host in PLACEHOLDER_AUTHORITY_HOSTS:
        return False
    if (
        host.endswith(".gov")
        or ".gov." in host
        or host.startswith("gov.")
        or host.endswith(".gouv.fr")
        or ".gouv." in host
        or host.startswith("gouv.")
        or host.endswith(".gc.ca")
        or re.search(r"(?:^|\.)state\.[a-z]{2}\.us$", host) is not None
    ):
        return True
    labels = host.split(".")
    if len(labels) >= 2 and labels[-2] == "go" and len(labels[-1]) == 2:
        # e.g. kra.go.ke, nta.go.jp, nts.go.kr — "go" as its own label directly
        # before a two-letter country code, not just "go" appearing inside a word.
        return True
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in NAMED_AUTHORITY_SUFFIXES)
