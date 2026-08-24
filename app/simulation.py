from __future__ import annotations

import random
from dataclasses import dataclass

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


def simulate(venture: Venture, inputs: SimulationInputs, runs: int) -> float:
    assumptions = venture.assumption_map()
    rng = random.Random(venture.id)
    founder = venture.intake.founder
    usable = founder.available_capital + founder.debt_available - founder.protected_reserve
    successes = 0
    for _ in range(runs):
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
        cash = usable - setup
        month_12_profit = float("-inf")
        survived = cash >= 0
        for month in range(1, 13):
            ramp = min(1.0, 0.55 + (month - 1) * 0.09)
            revenue = basket * tx * days * ramp
            month_12_profit = (
                revenue * margin - revenue * shrinkage - rent - payroll - utilities
            )
            cash += month_12_profit
            if cash < 0:
                survived = False
                break
        if survived and month_12_profit >= founder.target_monthly_owner_income:
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
    low, high = base * (1 - spread), base * (1 + spread)
    if lower_floor is not None:
        low = max(lower_floor, low)
    if upper_cap is not None:
        high = min(upper_cap, high)
    return rng.triangular(low, high, min(max(base, low), high))


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
