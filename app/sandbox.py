from __future__ import annotations

from copy import deepcopy

from app.domain import SandboxExperiment, SandboxRequest, Venture
from app.engine import VentureEngine


class SandboxRunner:
    """Runs disposable scenario shocks without mutating canonical venture state."""

    def __init__(self, engine: VentureEngine | None = None):
        self.engine = engine or VentureEngine()

    def run(self, venture: Venture, request: SandboxRequest) -> SandboxExperiment:
        baseline = deepcopy(venture)
        scenario = deepcopy(venture)
        baseline = self.engine.underwrite(baseline, simulation_runs=request.simulation_runs)

        assumptions = scenario.assumption_map()
        for key, value in request.shocks.items():
            if key not in assumptions:
                raise KeyError(f"Unknown assumption: {key}")
            assumptions[key].value = value
        scenario = self.engine.underwrite(scenario, simulation_runs=request.simulation_runs)

        return SandboxExperiment(
            venture_id=venture.id,
            name=request.name,
            shocks=request.shocks,
            simulation_runs=request.simulation_runs,
            baseline_probability=(
                baseline.underwriting.break_even_probability_12m if baseline.underwriting else None
            ),
            scenario_probability=(
                scenario.underwriting.break_even_probability_12m if scenario.underwriting else None
            ),
            baseline_decision=baseline.underwriting.decision if baseline.underwriting else None,
            scenario_decision=scenario.underwriting.decision if scenario.underwriting else None,
            notes=["Sandbox values are scenario inputs, never real-world evidence."],
        )
