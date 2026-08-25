from __future__ import annotations

from datetime import UTC, datetime

from app.domain import (
    Assumption,
    AssumptionCategory,
    Confidence,
    Decision,
    DecisionRecord,
    Evidence,
    GateStatus,
    MaterialChange,
    RoadmapStep,
    UnderwritingResult,
    Venture,
    VentureStatus,
)
from app.research import ResearchFinding
from app.simulation import (
    CONFIDENCE_SCORE,
    REQUIRED_FINANCIAL_KEYS,
    base_case,
    evidence_coverage,
    extract_inputs,
    model_confidence,
    risk_ranking,
    simulate,
)


class VentureEngine:
    REQUIRED_FINANCIAL_KEYS = REQUIRED_FINANCIAL_KEYS

    def initialise(self, venture: Venture) -> Venture:
        if venture.assumptions:
            return venture
        venture.assumptions = self._starter_assumptions(venture)
        venture.roadmap = self._starter_roadmap(venture)
        venture.status = VentureStatus.UNDERWRITE
        venture.updated_at = datetime.now(UTC)
        return venture

    def ingest_research(self, venture: Venture, findings: list[ResearchFinding]) -> Venture:
        assumptions = venture.assumption_map()
        for finding in findings:
            evidence = Evidence(
                assumption_key=finding.assumption_key,
                claim=finding.claim,
                value=finding.value,
                unit=finding.unit,
                evidence_type=finding.evidence_type,
                confidence=finding.confidence,
                source_title=finding.source_title,
                source_url=finding.source_url,
                notes=finding.notes,
            )
            venture.evidence.append(evidence)
            assumption = assumptions.get(finding.assumption_key)
            if assumption is None:
                assumption = Assumption(
                    key=finding.assumption_key,
                    label=self._pretty_key(finding.assumption_key),
                    category=self._category_for_key(finding.assumption_key),
                    critical=finding.assumption_key.startswith(("regulatory_", "execution_")),
                    impact_weight=1.2,
                )
                venture.assumptions.append(assumption)
                assumptions[assumption.key] = assumption
            if finding.value is not None:
                assumption.value = float(finding.value)
                assumption.unit = finding.unit
            if CONFIDENCE_SCORE[finding.confidence] >= CONFIDENCE_SCORE[assumption.confidence]:
                assumption.confidence = finding.confidence
            if evidence.id not in assumption.evidence_ids:
                assumption.evidence_ids.append(evidence.id)
            assumption.source_note = finding.claim
            assumption.updated_at = datetime.now(UTC)
        venture.status = VentureStatus.ATTACK
        venture.updated_at = datetime.now(UTC)
        return venture

    def add_evidence(self, venture: Venture, evidence: Evidence) -> Venture:
        assumption = venture.assumption_map().get(evidence.assumption_key or "")
        if assumption is None:
            raise KeyError(f"Unknown assumption: {evidence.assumption_key}")
        venture.evidence.append(evidence)
        assumption.evidence_ids.append(evidence.id)
        if isinstance(evidence.value, (int, float)) and not isinstance(evidence.value, bool):
            assumption.value = float(evidence.value)
            assumption.unit = evidence.unit or assumption.unit
        if CONFIDENCE_SCORE[evidence.confidence] >= CONFIDENCE_SCORE[assumption.confidence]:
            assumption.confidence = evidence.confidence
        assumption.source_note = evidence.claim
        assumption.updated_at = datetime.now(UTC)
        venture.updated_at = datetime.now(UTC)
        return self.underwrite(venture)

    def apply_change(self, venture: Venture, change: MaterialChange) -> Venture:
        if change.assumption_key:
            assumption = venture.assumption_map().get(change.assumption_key)
            if assumption is None:
                raise KeyError(f"Unknown assumption: {change.assumption_key}")
            change.old_value = assumption.value
            if change.new_value is not None:
                assumption.value = change.new_value
            assumption.confidence = change.confidence
            assumption.source_note = change.summary
            assumption.updated_at = change.occurred_at
        venture.changes.append(change)
        venture.status = VentureStatus.WATCH
        venture.updated_at = datetime.now(UTC)
        return self.underwrite(venture)

    def underwrite(self, venture: Venture, simulation_runs: int = 5000) -> Venture:
        assumptions = venture.assumption_map()
        critical_unknowns = [
            item.label
            for item in venture.assumptions
            if item.critical and self._assumption_unresolved(item)
        ]
        jurisdiction = venture.intake.jurisdiction
        if not jurisdiction.country_code:
            critical_unknowns.append("Country / legal jurisdiction")
        if not jurisdiction.currency_code:
            critical_unknowns.append("Operating currency")
        critical_unknowns = list(dict.fromkeys(critical_unknowns))

        coverage = evidence_coverage(venture)
        inputs = extract_inputs(assumptions)
        if inputs is None:
            result = UnderwritingResult(
                decision=Decision.NEEDS_DATA,
                break_even_probability_12m=0,
                evidence_coverage=coverage,
                model_confidence=model_confidence(coverage),
                critical_unknowns=critical_unknowns
                + self._missing_required_assumptions(assumptions),
                biggest_risks=["The financial model is incomplete."],
                rationale=[
                    "Required financial assumptions are still missing; the agent refuses to invent them."
                ],
                simulation_runs=0,
            )
        else:
            base = base_case(inputs, venture)
            probability = simulate(venture, inputs, simulation_runs)
            decision = self._decision(
                probability,
                critical_unknowns,
                base["capital_remaining"],
            )
            result = UnderwritingResult(
                decision=decision,
                break_even_probability_12m=probability,
                evidence_coverage=coverage,
                model_confidence=model_confidence(coverage),
                monthly_revenue_base=base["monthly_revenue"],
                monthly_operating_profit_base=base["monthly_operating_profit"],
                capital_remaining_after_setup=base["capital_remaining"],
                critical_unknowns=critical_unknowns,
                biggest_risks=risk_ranking(venture),
                rationale=self._rationale(
                    decision,
                    probability,
                    critical_unknowns,
                    base,
                    venture.intake.jurisdiction.money_unit(),
                ),
                simulation_runs=simulation_runs,
            )
        previous = venture.underwriting.decision if venture.underwriting else None
        venture.underwriting = result
        venture.status = self._status_from_decision(result.decision)
        self._refresh_roadmap(venture)
        if previous != result.decision:
            venture.decision_history.append(
                DecisionRecord(
                    decision=result.decision,
                    reason="; ".join(result.rationale[:2]),
                    assumption_snapshot={item.key: item.value for item in venture.assumptions},
                )
            )
        venture.updated_at = datetime.now(UTC)
        return venture

    def complete_roadmap_step(self, venture: Venture, step_id: str) -> Venture:
        step = next((item for item in venture.roadmap if item.id == step_id), None)
        if step is None:
            raise KeyError("Roadmap step not found")
        if step.status == GateStatus.LOCKED:
            raise ValueError("Roadmap step is still locked")
        step.status = GateStatus.COMPLETE
        step.completed_at = datetime.now(UTC)
        self._refresh_roadmap(venture)
        if all(item.status == GateStatus.COMPLETE for item in venture.roadmap):
            venture.status = VentureStatus.WATCH
        venture.updated_at = datetime.now(UTC)
        return venture

    @staticmethod
    def _starter_assumptions(venture: Venture) -> list[Assumption]:
        currency = venture.intake.jurisdiction.money_unit()
        specs = [
            ("setup_costs", "Total setup and opening inventory cost", AssumptionCategory.CAPITAL, currency, True, 2.0),
            ("monthly_rent", "Monthly premises rent", AssumptionCategory.LOCATION, f"{currency}/month", True, 1.8),
            ("monthly_payroll", "Monthly payroll", AssumptionCategory.COST, f"{currency}/month", False, 1.2),
            ("monthly_utilities", "Monthly utilities and overhead", AssumptionCategory.COST, f"{currency}/month", False, 1.0),
            ("gross_margin_pct", "Blended gross margin", AssumptionCategory.MARGIN, "ratio", True, 2.0),
            ("average_basket", "Average customer transaction value", AssumptionCategory.DEMAND, f"{currency}/transaction", True, 2.0),
            ("transactions_per_day", "Daily transactions / demand", AssumptionCategory.DEMAND, "transactions/day", True, 3.0),
            ("days_open_month", "Trading days per month", AssumptionCategory.OPERATIONS, "days/month", False, 0.6),
            ("shrinkage_pct", "Shrinkage, spoilage or wastage rate", AssumptionCategory.OPERATIONS, "ratio", False, 1.0),
            ("regulatory_registration_path", "Registration and legal operating path", AssumptionCategory.REGULATORY, None, True, 1.5),
            ("competition_local", "Material local competitors", AssumptionCategory.COMPETITION, None, True, 1.7),
        ]
        return [
            Assumption(
                key=key,
                label=label,
                category=category,
                unit=unit,
                critical=critical,
                impact_weight=weight,
            )
            for key, label, category, unit, critical, weight in specs
        ]

    @staticmethod
    def _starter_roadmap(venture: Venture) -> list[RoadmapStep]:
        location = venture.intake.location
        idea = venture.intake.idea
        country = venture.intake.jurisdiction.country_name or venture.intake.jurisdiction.country_code or "the target jurisdiction"
        rows = [
            ("FEASIBILITY", "Close the critical evidence gaps", "Resolve assumptions capable of killing the business before committing capital.", False, False, None, False),
            ("LOCATION", "Validate the proposed location", "Verify rent, access, demand and direct competitor pressure for the actual premises.", True, True, f"Commercial premises and local competition for {idea} in {location}", False),
            ("REGISTRATION", "Confirm and complete business registration", "Use official sources to identify the correct national/subnational registration route and required documents.", False, True, f"Official business registration requirements for {idea} in {location}, {country}", True),
            ("TAX", "Confirm tax registrations and recurring obligations", "Identify obligations supported by the competent national, state/provincial and local tax authorities.", False, True, f"Official tax registration and recurring tax obligations for {idea} in {location}, {country}", True),
            ("LICENSING", "Resolve local and sector licences", "Find actual permits, licences, inspections, fees and prerequisites at every applicable jurisdictional level.", True, True, f"Official national, state/provincial, municipal/local and sector licences for {idea} in {location}, {country}", True),
            ("SUPPLIERS", "Source and compare suppliers", "Obtain comparable quotes and replace estimates with verified commercial terms.", False, False, f"Suppliers and wholesalers relevant to {idea} near {location}", False),
            ("SERVICES", "Find execution providers", "Identify professionals, installers or providers required by unresolved dependencies.", False, False, f"Service providers required to launch {idea} near {location}", False),
            ("PREMISES", "Commit to premises only after the location gate passes", "Do not sign a lease while a critical location or demand assumption remains weak.", True, True, None, False),
            ("LAUNCH", "Execute launch sequence", "Order fit-out, equipment, inventory, staffing, payments and opening tasks in dependency order.", True, True, None, False),
            ("WATCH", "Monitor the live venture thesis", "Re-run the model when costs, competitors, regulation, demand or operating data changes.", False, False, None, False),
        ]
        steps = [
            RoadmapStep(
                phase=phase,
                title=title,
                description=description,
                status=GateStatus.READY if index == 0 else GateStatus.LOCKED,
                irreversible=irreversible,
                requires_user_approval=approval,
                research_query=query,
                official_source_required=official,
            )
            for index, (
                phase,
                title,
                description,
                irreversible,
                approval,
                query,
                official,
            ) in enumerate(rows)
        ]
        for index in range(1, len(steps)):
            steps[index].dependency_ids = [steps[index - 1].id]
        return steps

    def _refresh_roadmap(self, venture: Venture) -> None:
        if not venture.roadmap:
            return
        critical = venture.underwriting.critical_unknowns if venture.underwriting else []
        decision = venture.underwriting.decision if venture.underwriting else Decision.NEEDS_DATA
        statuses = {step.id: step.status for step in venture.roadmap}
        for index, step in enumerate(venture.roadmap):
            if step.status == GateStatus.COMPLETE:
                statuses[step.id] = step.status
                continue
            if index == 0:
                step.status = GateStatus.READY
            else:
                deps_complete = all(
                    statuses.get(dependency) == GateStatus.COMPLETE
                    for dependency in step.dependency_ids
                )
                blocked = step.irreversible and (
                    bool(critical)
                    or decision in {Decision.REJECT, Decision.NEEDS_DATA}
                )
                step.status = GateStatus.READY if deps_complete and not blocked else GateStatus.LOCKED
            statuses[step.id] = step.status

    def _missing_required_assumptions(
        self,
        assumptions: dict[str, Assumption],
    ) -> list[str]:
        return [
            self._pretty_key(key)
            for key in sorted(self.REQUIRED_FINANCIAL_KEYS)
            if assumptions.get(key) is None or assumptions[key].value is None
        ]

    @staticmethod
    def _assumption_unresolved(item: Assumption) -> bool:
        if item.confidence in {Confidence.UNKNOWN, Confidence.LOW}:
            return True
        if item.unit is not None and item.value is None:
            return True
        if item.unit is None and not item.evidence_ids:
            return True
        return False

    @staticmethod
    def _decision(
        probability: float,
        critical_unknowns: list[str],
        capital_remaining: float,
    ) -> Decision:
        if capital_remaining < 0 or probability < 0.25:
            return Decision.REJECT
        if critical_unknowns:
            return Decision.CONDITIONAL
        if probability >= 0.7:
            return Decision.APPROVE
        if probability >= 0.4:
            return Decision.CONDITIONAL
        return Decision.REJECT

    @staticmethod
    def _status_from_decision(decision: Decision) -> VentureStatus:
        return {
            Decision.REJECT: VentureStatus.KILLED,
            Decision.APPROVE: VentureStatus.EXECUTE,
            Decision.CONDITIONAL: VentureStatus.OPTIMISE,
            Decision.NEEDS_DATA: VentureStatus.UNDERWRITE,
        }[decision]

    @staticmethod
    def _rationale(
        decision: Decision,
        probability: float,
        critical_unknowns: list[str],
        base: dict[str, float],
        currency: str,
    ) -> list[str]:
        lines = [
            f"The model produced a {probability:.0%} probability of surviving cash burn and reaching the founder's month-12 operating-income target under current assumptions.",
            f"Base-case capital remaining after setup is {base['capital_remaining']:,.0f} {currency}.",
        ]
        if critical_unknowns:
            lines.append(
                "The decision is constrained by critical weak/unknown assumptions: "
                + ", ".join(critical_unknowns[:4])
                + "."
            )
        if decision == Decision.REJECT:
            lines.append("Change the current configuration before irreversible capital is committed.")
        elif decision == Decision.APPROVE:
            lines.append("No critical weak assumption currently blocks execution.")
        else:
            lines.append("Proceed only after the listed evidence gaps are resolved and the model is re-run.")
        return lines

    @staticmethod
    def _category_for_key(key: str) -> AssumptionCategory:
        if key.startswith("regulatory_"):
            return AssumptionCategory.REGULATORY
        if key.startswith("execution_"):
            return AssumptionCategory.EXECUTION
        if "compet" in key:
            return AssumptionCategory.COMPETITION
        return AssumptionCategory.EXECUTION

    @staticmethod
    def _pretty_key(key: str) -> str:
        return key.replace("_", " ").strip().title()
