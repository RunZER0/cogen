from __future__ import annotations

from dataclasses import dataclass

from app.domain import SpecialistReport, SpecialistRole, Venture
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


@dataclass(slots=True)
class OrchestrationResult:
    findings: list[ResearchFinding]
    reports: list[SpecialistReport]
    contradictions: list
    rejected: list[str]


class SpecialistOrchestrator:
    """Coordinates narrow specialists around one canonical Venture Twin.

    Specialists never own separate venture state and do not vote. They only return candidate evidence.
    Deterministic evidence admission and underwriting decide what changes canonical state.
    """

    def __init__(
        self,
        provider: ResearchProvider,
        *,
        roles: list[SpecialistRole] | None = None,
        ledger: EvidenceLedger | None = None,
    ):
        self.provider = provider
        self.roles = roles or list(SpecialistRole)
        self.ledger = ledger or EvidenceLedger()

    def run(self, venture: Venture, workflow_id: str) -> OrchestrationResult:
        findings: list[ResearchFinding] = []
        reports: list[SpecialistReport] = []
        contradictions = []
        rejected: list[str] = []
        seen: set[tuple] = set()

        for role in self.roles:
            raw = self.provider.research(venture, role=role, mandate=MANDATES[role])
            prepared = self.ledger.prepare(venture, raw, role=role)
            accepted_for_role: list[ResearchFinding] = []
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
                accepted_for_role.append(item)
            findings.extend(accepted_for_role)
            contradictions.extend(prepared.contradictions)
            rejected.extend(prepared.rejected)
            reports.append(
                SpecialistReport(
                    venture_id=venture.id,
                    workflow_id=workflow_id,
                    role=role,
                    mandate=MANDATES[role],
                    finding_count=len(accepted_for_role),
                    rejected_count=len(prepared.rejected),
                    summary=(
                        f"{role.value} specialist produced {len(accepted_for_role)} admissible findings; "
                        f"{len(prepared.rejected)} were rejected by evidence policy."
                    ),
                )
            )

        return OrchestrationResult(
            findings=findings,
            reports=reports,
            contradictions=contradictions,
            rejected=rejected,
        )
