from __future__ import annotations

from dataclasses import dataclass

from app.domain import (
    AssumptionCategory,
    Confidence,
    SpecialistReport,
    SpecialistRole,
    Venture,
)
from app.evidence import EvidenceLedger
from app.research import ResearchFinding, ResearchProvider


MANDATES: dict[SpecialistRole, str] = {
    SpecialistRole.FINANCE: (
        "Establish the cost/margin variables that can break the founder's capital and owner-income target."
    ),
    SpecialistRole.MARKET: (
        "Attack demand, location and competition assumptions using current local evidence and demand proxies."
    ),
    SpecialistRole.REGULATORY: (
        "Establish registration, tax, county and sector obligations from primary official sources only."
    ),
    SpecialistRole.EXECUTION: (
        "Find concrete launch dependencies, suppliers and service-provider categories the founder will need."
    ),
    SpecialistRole.ADVERSARY: (
        "Build the strongest evidence-backed case for rejecting or reconfiguring this venture."
    ),
}

ROLE_CATEGORIES: dict[SpecialistRole, set[AssumptionCategory]] = {
    SpecialistRole.FINANCE: {
        AssumptionCategory.CAPITAL,
        AssumptionCategory.COST,
        AssumptionCategory.MARGIN,
        AssumptionCategory.OPERATIONS,
    },
    SpecialistRole.MARKET: {
        AssumptionCategory.DEMAND,
        AssumptionCategory.LOCATION,
        AssumptionCategory.COMPETITION,
    },
    SpecialistRole.REGULATORY: {AssumptionCategory.REGULATORY},
    SpecialistRole.EXECUTION: {AssumptionCategory.EXECUTION},
    SpecialistRole.ADVERSARY: set(AssumptionCategory),
}

CONFIDENCE_RANK = {
    Confidence.UNKNOWN: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.VERIFIED: 4,
}


@dataclass(slots=True)
class OrchestrationResult:
    findings: list[ResearchFinding]
    reports: list[SpecialistReport]
    contradictions: list
    rejected: list[str]


class SpecialistOrchestrator:
    """Coordinates scoped specialists around one canonical Venture Twin.

    A specialist gets a bounded research budget and may perform a targeted follow-up after observing the
    first pass. Specialists still never own separate venture state and do not vote. They return candidate
    evidence only; deterministic evidence admission and underwriting own canonical mutations.
    """

    def __init__(
        self,
        provider: ResearchProvider,
        *,
        roles: list[SpecialistRole] | None = None,
        ledger: EvidenceLedger | None = None,
        max_rounds: int = 2,
    ):
        self.provider = provider
        self.roles = roles or list(SpecialistRole)
        self.ledger = ledger or EvidenceLedger()
        self.max_rounds = max(1, min(max_rounds, 3))

    def run(self, venture: Venture, workflow_id: str) -> OrchestrationResult:
        findings: list[ResearchFinding] = []
        reports: list[SpecialistReport] = []
        contradictions = []
        rejected: list[str] = []
        seen: set[tuple] = set()

        for role in self.roles:
            accepted_for_role: list[ResearchFinding] = []
            rejected_for_role: list[str] = []
            rounds_used = 0
            mandate = MANDATES[role]

            for round_number in range(1, self.max_rounds + 1):
                rounds_used = round_number
                raw = self.provider.research(venture, role=role, mandate=mandate)
                prepared = self.ledger.prepare(venture, raw, role=role)
                newly_accepted: list[ResearchFinding] = []
                for item in prepared.accepted:
                    key = (
                        item.assumption_key,
                        item.claim.strip().lower(),
                        item.value,
                        item.source_url,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    newly_accepted.append(item)

                accepted_for_role.extend(newly_accepted)
                findings.extend(newly_accepted)
                contradictions.extend(prepared.contradictions)
                rejected.extend(prepared.rejected)
                rejected_for_role.extend(prepared.rejected)

                if round_number >= self.max_rounds:
                    break
                focus = self._focus_keys(venture, role, accepted_for_role)
                if not focus:
                    break
                prior_sources = sorted(
                    {item.source_url for item in accepted_for_role if item.source_url}
                )
                mandate = (
                    f"Follow-up round. Resolve or falsify these still-material assumptions: {focus}. "
                    "Seek stronger or independent evidence, not paraphrases of the first pass. "
                    f"Already used source URLs: {prior_sources[:6]}. "
                    "Stop rather than manufacture certainty if reliable evidence is unavailable."
                )

            reports.append(
                SpecialistReport(
                    venture_id=venture.id,
                    workflow_id=workflow_id,
                    role=role,
                    mandate=MANDATES[role],
                    finding_count=len(accepted_for_role),
                    rejected_count=len(rejected_for_role),
                    summary=(
                        f"{role.value} specialist used {rounds_used} bounded research round(s), produced "
                        f"{len(accepted_for_role)} admissible findings; {len(rejected_for_role)} were "
                        "rejected by evidence policy."
                    ),
                )
            )

        return OrchestrationResult(
            findings=findings,
            reports=reports,
            contradictions=contradictions,
            rejected=rejected,
        )

    @staticmethod
    def _focus_keys(
        venture: Venture,
        role: SpecialistRole,
        accepted: list[ResearchFinding],
    ) -> list[str]:
        resolved = {
            item.assumption_key
            for item in accepted
            if CONFIDENCE_RANK[item.confidence] >= CONFIDENCE_RANK[Confidence.MEDIUM]
        }
        categories = ROLE_CATEGORIES[role]
        candidates = [
            item
            for item in venture.assumptions
            if item.category in categories
            and item.key not in resolved
            and (
                item.stale
                or item.critical
                or item.confidence in {Confidence.UNKNOWN, Confidence.LOW}
            )
        ]
        candidates.sort(
            key=lambda item: (
                1 if item.critical else 0,
                1 if item.stale else 0,
                item.impact_weight,
                -CONFIDENCE_RANK[item.confidence],
            ),
            reverse=True,
        )
        return [item.key for item in candidates[:4]]
