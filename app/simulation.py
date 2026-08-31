from __future__ import annotations

import random
from dataclasses import dataclass, replace

from app.domain import Assumption, Confidence, Venture

CONFIDENCE_SCORE = {
    Confidence.UNKNOWN: 0.0,
    Confidence.LOW: 0.25,
    Confidence.MEDIUM: 0.55,
    Confidence.HIGH: 0.8,
    Confidence.VERIFIED: 1.0,
}
UNCERTAINTY = {
    Confidence.UNKNOWN: 0.65,
    Confidence.LOW: 0.45,
    Confidence.MEDIUM: 0.25,
    Confidence.HIGH: 0.12,
    Confidence.VERIFIED: 0.05,
}
REQUIRED_FINANCIAL_KEYS = {
    "setup_costs",
    "monthly_rent",
    "monthly_payroll",
    "monthly_utilities",
    "gross_margin_pct",
    "average_basket",
    "transactions_per_day",
    "days_open_month",
    "shrinkage_pct",
}


@dataclass(slots=True)
class SimulationInputs:
    setup_costs: float
    monthly_rent: float
    monthly_payroll: float
    monthly_utilities: float
    gross_margin_pct: float
    average_basket: float
    transactions_per_day: float
    days_open_month: float
    shrinkage_pct: float


def extract_inputs(assumptions: dict[str, Assumption]) -> SimulationInputs | None:
    if any(assumptions.get(key) is None or assumptions[key].value is None for key in REQUIRED_FINANCIAL_KEYS):
        return None
    return SimulationInputs(
        **{key: float(assumptions[key].value) for key in REQUIRED_FINANCIAL_KEYS}
    )


def base_case(inputs: SimulationInputs, venture: Venture) -> dict[str, float]:
    revenue = inputs.average_basket * inputs.transactions_per_day * inputs.days_open_month
    profit = (
        revenue * inputs.gross_margin_pct
        - revenue * inputs.shrinkage_pct
        - inputs.monthly_rent
        - inputs.monthly_payroll
        - inputs.monthly_utilities
    )
    founder = venture.intake.founder
    usable = founder.available_capital + founder.debt_available - founder.protected_reserve
    return {
        "monthly_revenue": revenue,
        "monthly_operating_profit": profit,
        "capital_remaining": usable - inputs.setup_costs,
    }


def _failure_rate(assumptions: dict[str, Assumption]) -> float | None:
    """The venture's own market evidence on how often this kind of business fails, if any.

    Research specialists surface failure-rate statistics (e.g. "specialty coffee shops have an
    80% two-year failure rate") as assumptions. The Monte Carlo should honor that evidence rather
    than ignore it: a venture whose own evidence base says 80% of comparable businesses fail
    cannot report an 82% survival probability with a straight face. Returns None when no
    failure-rate assumption is present, so the sim falls back to pure cash-flow survival.
    """
    for key in ("failure_rate_two_year", "specialty_coffee_failure_rate_2yr", "failure_rate"):
        assumption = assumptions.get(key)
        if assumption is not None and assumption.value is not None:
            rate = float(assumption.value)
            if 0.0 <= rate <= 1.0:
                return rate
    return None


def simulate(venture: Venture, inputs: SimulationInputs, runs: int) -> float:
    assumptions = venture.assumption_map()
    rng = random.Random(venture.id)
    founder = venture.intake.founder
    usable = founder.available_capital + founder.debt_available - founder.protected_reserve
    failure_rate = _failure_rate(assumptions)
    successes = 0
    for _ in range(runs):
        # A real business lives under ONE persistent demand and cost regime, not a fresh random
        # draw every month. Sampling demand/costs once per run (not once per month) is what makes
        # a weak-demand year stay weak and a high-cost year stay high — without this, a positive
        # mean always produces positive cash and the survival metric saturates to a constant
        # regardless of how much demand or rent actually changes. This is the fix for the
        # observed 55-vs-200-transactions/day insensitivity.
        setup = sample(rng, inputs.setup_costs, assumptions["setup_costs"].confidence, 0)
        rent = sample(rng, inputs.monthly_rent, assumptions["monthly_rent"].confidence, 0)
        payroll = sample(rng, inputs.monthly_payroll, assumptions["monthly_payroll"].confidence, 0)
        utilities = sample(
            rng,
            inputs.monthly_utilities,
            assumptions["monthly_utilities"].confidence,
            0,
        )
        margin = sample(
            rng,
            inputs.gross_margin_pct,
            assumptions["gross_margin_pct"].confidence,
            0.01,
            0.95,
        )
        basket = sample(rng, inputs.average_basket, assumptions["average_basket"].confidence, 1)
        tx = sample(
            rng,
            inputs.transactions_per_day,
            assumptions["transactions_per_day"].confidence,
            0,
        )
        days = sample(
            rng,
            inputs.days_open_month,
            assumptions["days_open_month"].confidence,
            1,
            31,
        )
        shrinkage = sample(
            rng,
            inputs.shrinkage_pct,
            assumptions["shrinkage_pct"].confidence,
            0,
            0.5,
        )
        # Going-concern risk: if the venture's own market evidence says comparable businesses
        # fail at a material rate, that is a real survival risk independent of this run's cash
        # flow. A run that draws a "fails anyway" outcome is counted as a failure regardless of
        # how the arithmetic plays out — the model must not report a high survival probability
        # that its own failure-rate evidence contradicts.
        if failure_rate is not None and rng.random() < failure_rate:
            continue
        cash = usable - setup
        cash_min = cash
        # Track trough drawdown AND whether operator income is met once the business has
        # matured. A venture that only reaches the founder's income target in one lucky month is
        # not "surviving" — the target must be met on a stabilized, ongoing basis.
        months_at_target = 0
        month_12_profit = float("-inf")
        survived = cash >= 0
        for month in range(1, 13):
            ramp = min(1.0, 0.55 + (month - 1) * 0.09)
            revenue = basket * tx * days * ramp
            month_12_profit = (
                revenue * margin - revenue * shrinkage - rent - payroll - utilities
            )
            cash += month_12_profit
            cash_min = min(cash_min, cash)
            if month_12_profit >= founder.target_monthly_owner_income:
                months_at_target += 1
            if cash < 0:
                survived = False
                break
        # Must (a) never run out of cash, (b) keep a positive operating cushion, and (c) meet the
        # operator-income target across the stabilized half of the first year — not just month 12.
        stable_months = max(1, 6)
        income_met = months_at_target >= stable_months
        if survived and cash_min > 0 and income_met:
            successes += 1
    return round(successes / runs, 4) if runs else 0.0


def sample(
    rng: random.Random,
    base: float,
    confidence: Confidence,
    lower_floor: float | None = None,
    upper_cap: float | None = None,
) -> float:
    spread = UNCERTAINTY[confidence]
    # For LOW/MEDIUM confidence inputs the arithmetic midpoint is not trustworthy: a single
    # optimistic market estimate is not a reliable centre of mass — real-world economics skew
    # toward WORSE outcomes than advertised (missed targets, cost overruns, softer demand). We
    # push the triangular mode below the base proportionally to uncertainty, so unverified
    # assumptions produce a heavy downside tail instead of a symmetric band around the
    # sometimes-optimistic figure. VERIFIED/HIGH inputs stay near base (their small spread is
    # already honest). This is the structural reason the old 77.5%-margin model was
    # unkillable: a symmetric band around a single optimistic margin can never go negative.
    if confidence in {Confidence.UNKNOWN, Confidence.LOW, Confidence.MEDIUM}:
        mode = base * (1 - spread * 0.6)
    else:
        mode = base
    low, high = base * (1 - spread), base * (1 + spread)
    if lower_floor is not None:
        low = max(lower_floor, low)
    if upper_cap is not None:
        high = min(upper_cap, high)
    mode = min(max(mode, low), high)
    return rng.triangular(low, high, mode)


def evidence_coverage(venture: Venture) -> float:
    total = sum(item.impact_weight for item in venture.assumptions)
    if not total:
        return 0.0
    score = sum(
        item.impact_weight * CONFIDENCE_SCORE[item.confidence]
        for item in venture.assumptions
    )
    return round(score / total, 4)


def model_confidence(coverage: float) -> Confidence:
    if coverage >= 0.85:
        return Confidence.HIGH
    if coverage >= 0.6:
        return Confidence.MEDIUM
    if coverage >= 0.3:
        return Confidence.LOW
    return Confidence.UNKNOWN


def identify_rescue_candidate(venture: Venture) -> dict | None:
    """The single assumption shift that most improves monthly operating profit, among the
    assumptions still uncertain enough to plausibly move — a server-side port of web/app.js's
    client-only rescueCandidates() sensitivity sweep (worst-lever branch).

    This exists so the fix isn't just a number sitting in a UI button nobody has clicked yet: it
    is surfaced back to the agent in the same tool-result envelope that already reports the
    underwriting decision (see app/agent.py's _citation_envelope), so a REJECT/CONDITIONAL turn
    carries its own next step instead of leaving the founder to notice "What could make this
    work" and click "Test in sandbox" themselves. Returns None when the base case is already at
    or above zero (nothing to rescue on this axis) or no candidate shift actually helps.
    """
    assumptions = venture.assumption_map()
    inputs = extract_inputs(assumptions)
    if inputs is None:
        return None
    base = base_case(inputs, venture)
    if base["monthly_operating_profit"] >= 0:
        return None

    best: dict | None = None
    # 1. First, check assumptions that are not yet verified (using their uncertainty spread)
    for key in REQUIRED_FINANCIAL_KEYS - {"setup_costs"}:
        assumption = assumptions.get(key)
        if assumption is None or assumption.confidence == Confidence.VERIFIED:
            continue  # nothing plausible left to move
        spread = UNCERTAINTY[assumption.confidence]
        base_value = getattr(inputs, key)
        for candidate_value in (base_value * (1 - spread), base_value * (1 + spread)):
            trial = base_case(replace(inputs, **{key: candidate_value}), venture)
            gain = trial["monthly_operating_profit"] - base["monthly_operating_profit"]
            if gain > 0 and (best is None or gain > best["gain"]):
                best = {
                    "assumption_key": key,
                    "assumption_label": assumption.label,
                    "shocked_value": round(candidate_value, 2),
                    "monthly_operating_profit_at_shock": round(trial["monthly_operating_profit"], 2),
                    "gain": round(gain, 2),
                }

    # 2. If all assumptions are verified or no single uncertain spread shift rescued profit,
    # perform a sensitivity sweep across all operational/margin levers to find the single largest
    # structural lever (e.g. gross margin expansion for service pivot, volume expansion, or overhead reduction).
    if best is None:
        for key in REQUIRED_FINANCIAL_KEYS - {"setup_costs"}:
            assumption = assumptions.get(key)
            if assumption is None:
                continue
            base_value = getattr(inputs, key)
            if base_value == 0 and key != "gross_margin_pct":
                continue
            if key == "gross_margin_pct":
                candidate_values = [min(0.95, base_value * 1.5), min(0.95, base_value * 3.0), min(0.95, base_value * 8.0), 0.45]
            elif key == "shrinkage_pct":
                candidate_values = [max(0.0, base_value * 0.5)]
            else:
                candidate_values = [base_value * 0.5, base_value * 0.75, base_value * 1.25, base_value * 1.5]

            for candidate_value in candidate_values:
                trial = base_case(replace(inputs, **{key: candidate_value}), venture)
                gain = trial["monthly_operating_profit"] - base["monthly_operating_profit"]
                if gain > 0 and (best is None or gain > best["gain"]):
                    best = {
                        "assumption_key": key,
                        "assumption_label": assumption.label,
                        "shocked_value": round(candidate_value, 2),
                        "monthly_operating_profit_at_shock": round(trial["monthly_operating_profit"], 2),
                        "gain": round(gain, 2),
                    }

    return best


def risk_ranking(venture: Venture) -> list[str]:
    ranked = sorted(
        venture.assumptions,
        key=lambda item: item.impact_weight * (1 - CONFIDENCE_SCORE[item.confidence]),
        reverse=True,
    )
    return [
        f"{item.label}: {item.confidence.value} confidence"
        for item in ranked[:5]
        if item.confidence != Confidence.VERIFIED
    ]
