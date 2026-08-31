"""The sandbox subagent: an ADK agent that decides what a what-if scenario needs, optionally
grounds it in a real comparable via search, then runs the deterministic Monte Carlo shock
(app/sandbox.py) — it never invents the probability numbers itself, only what to test and, when
useful, what real-world fact to check before testing it.

Mirrors app/agentic_research.py's pattern (a small Agent + Runner + throwaway InMemorySessionService
per call) rather than the durable, multi-turn founder chat session in app/main.py — one sandbox
run is one bounded task, not an ongoing relationship.
"""
from __future__ import annotations

import json
from typing import Any

from app.domain import SandboxRequest, Venture
from app.subagents import EmitFn

_APP_NAME = "cogen-sandbox"


def _build_shock_tool(venture: Venture, emit: EmitFn):
    def run_shock(name: str, shocks_json: str, simulation_runs: int = 5000) -> str:
        """Run the deterministic Monte Carlo shock against this venture and return the result.

        shocks_json must be a JSON object of {assumption_key: new_value}. This is the only source
        of the actual probability numbers — never state a probability you did not get from this.
        """
        from app.runtime import get_service

        request = SandboxRequest(
            name=name, shocks=json.loads(shocks_json), simulation_runs=simulation_runs,
        )
        # Goes through the same service.run_sandbox() the direct /api/.../sandbox route and the
        # "Test in sandbox" button use — not a bare SandboxRunner() — so a scenario run from chat
        # is persisted (SandboxExperiment + EXPERIMENT_COMPLETED event) and shows up in "Past
        # experiments" exactly like any other, regardless of which surface triggered it.
        experiment = get_service().run_sandbox(venture.id, request)
        emit(
            "tool_result",
            name="run_shock",
            result_summary=(
                f"baseline={experiment.baseline_probability} scenario={experiment.scenario_probability}"
            ),
        )
        return experiment.model_dump_json()

    return run_shock


async def run_sandbox_subagent(
    venture: Venture, request: SandboxRequest, emit: EmitFn,
) -> dict[str, Any]:
    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    from app.agent_tools import as_async_tool, browse_page_for_details, search_web
    from app.llm import build_agent_model
    from app.settings import get_settings

    shock_tool = _build_shock_tool(venture, emit)
    agent = Agent(
        name="sandbox_specialist",
        model=build_agent_model(get_settings()),
        instruction=f"""
You are Cogen's sandbox specialist. You test exactly one what-if scenario against an existing
venture without touching its real evidence: "{request.name}", shocking {request.shocks} for
{venture.intake.idea} in {venture.intake.location}.

If the scenario references a real-world fact worth checking against a current source (a rent
comparable, a competitor's price, a supplier's quote) and it is not already fully specified by the
shock values given, use search_web / browse_page_for_details once or twice to sanity-check it —
but never let research change the numbers already given in the scenario itself; use it only to
annotate or caveat them in your final summary.

Then call run_shock with name={json.dumps(request.name)}, shocks_json={json.dumps(json.dumps(request.shocks))},
simulation_runs={request.simulation_runs}. This is the ONLY source of the actual probability
numbers — never state a probability you did not get back from run_shock, and call it exactly once.

Finish with one short paragraph for the founder: what the scenario tested, what run_shock
returned (baseline vs. scenario probability and decision), and what it means. State plainly that
this is disposable scenario output, never real-world evidence.
""".strip(),
        tools=[as_async_tool(shock_tool), as_async_tool(search_web), as_async_tool(browse_page_for_details)],
    )
    sessions = InMemorySessionService()
    session_id = f"{venture.id}:sandbox:{request.name}:{id(request)}"
    await sessions.create_session(app_name=_APP_NAME, user_id="sandbox", session_id=session_id)
    adk_runner = Runner(agent=agent, app_name=_APP_NAME, session_service=sessions)

    content = genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="Begin.")])
    final_text = ""
    async for event in adk_runner.run_async(user_id="sandbox", session_id=session_id, new_message=content):
        for call in event.get_function_calls() or []:
            emit("tool_call", name=call.name, args=call.args or {})
        if event.content and event.content.parts:
            spoken = "".join(part.text for part in event.content.parts if getattr(part, "text", None))
            if spoken.strip():
                if event.is_final_response():
                    final_text = spoken
                    emit("final", text=spoken)
                else:
                    emit("text", text=spoken)
    return {"narrative": final_text}
