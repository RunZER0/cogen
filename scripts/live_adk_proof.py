from __future__ import annotations

import asyncio
import json

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.runtime import get_service


PROMPT = """I have USD 85,000 and want to open a specialty coffee shop in Austin, Texas, United States.
Treat Austin as the locality, Texas as the state/subdivision, United States as the country, and model every
monetary value in USD. I must keep USD 15,000 untouched as protected reserve. No debt is available. I need
the business eventually to pay me USD 7,500 per month. I am a first-time cafe operator working full-time. I
will not accept losing more than USD 30,000, and I want to launch within 6 months. Use your Cogen tools now:
persist this as a Venture Twin, perform current evidence-grounded underwriting for Austin/Texas/US, attack the
idea rather than flatter it, do not invent any missing fact, and tell me what can kill it and whether I should
commit capital. Do not stop at a generic business plan."""


async def run_agent() -> tuple[list[str], str]:
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="app",
        user_id="live-proof-user",
        session_id="austin-coffee-proof",
    )
    runner = Runner(
        agent=root_agent,
        app_name="app",
        session_service=session_service,
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=PROMPT)],
    )

    tool_calls: list[str] = []
    final_text_parts: list[str] = []

    async for event in runner.run_async(
        user_id="live-proof-user",
        session_id=session.id,
        new_message=message,
    ):
        calls = event.get_function_calls()
        if calls:
            for call in calls:
                tool_calls.append(call.name)
                print("ADK_TOOL_CALL", call.name, json.dumps(call.args or {}, default=str))
        responses = event.get_function_responses()
        if responses:
            for response in responses:
                print("ADK_TOOL_RESPONSE", response.name)
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text_parts.append(part.text)

    return tool_calls, "\n".join(final_text_parts)


def prove_neon_state() -> None:
    service = get_service()
    ventures = [
        venture
        for venture in service.list_ventures()
        if venture.intake.country == "United States"
        and venture.intake.currency == "USD"
        and "Austin" in venture.intake.location
    ]
    if not ventures:
        raise SystemExit("ADK run did not persist an Austin/US/USD Venture Twin")

    venture = max(ventures, key=lambda item: item.created_at)
    units = [item.unit or "" for item in venture.assumptions]
    evidence_text = json.dumps(
        [item.model_dump(mode="json") for item in venture.evidence], default=str
    ).lower()
    specialists = service.specialists(venture.id)
    events = service.events(venture.id)
    validation = service.validation_tasks(venture.id)
    result = venture.underwriting

    if any("KES" in unit for unit in units):
        raise SystemExit("Country leak: KES appeared in US venture assumptions")
    if "kenya" in evidence_text or "kra" in evidence_text:
        raise SystemExit("Country leak: Kenyan evidence appeared in US venture")
    if result is None:
        raise SystemExit("ADK run created venture but did not underwrite it")

    roles = sorted({item.role.value for item in specialists})
    expected_roles = {"finance", "market", "regulatory", "execution", "adversary"}
    if set(roles) != expected_roles:
        raise SystemExit(f"Expected all five specialist roles, got {roles}")

    print("LIVE_ADK_NEON_PROOF_PASS")
    print("venture_id=", venture.id)
    print("idea=", venture.intake.idea)
    print("location=", venture.intake.location)
    print("country=", venture.intake.country)
    print("subdivision=", venture.intake.subdivision)
    print("currency=", venture.intake.currency)
    print("decision=", result.decision.value)
    print("simulation_runs=", result.simulation_runs)
    print("evidence_count=", len(venture.evidence))
    print("evidence_coverage=", result.evidence_coverage)
    print("model_confidence=", result.model_confidence.value)
    print("critical_unknowns=", result.critical_unknowns)
    print("biggest_risks=", result.biggest_risks)
    print("specialist_roles=", roles)
    print("validation_tasks=", [item.title for item in validation])
    print("event_types=", [item.event_type.value for item in events])
    print(
        "source_urls=",
        [str(item.source_url) for item in venture.evidence if item.source_url][:20],
    )


async def main() -> None:
    tool_calls, final_text = await asyncio.wait_for(run_agent(), timeout=900)
    print("ADK_TOOL_CALL_SEQUENCE=", tool_calls)
    print("ADK_FINAL_RESPONSE_BEGIN")
    print(final_text)
    print("ADK_FINAL_RESPONSE_END")

    if "create_venture" not in tool_calls:
        raise SystemExit(f"Root ADK agent never called create_venture: {tool_calls}")
    if "run_underwriting" not in tool_calls:
        raise SystemExit(f"Root ADK agent never called run_underwriting: {tool_calls}")

    prove_neon_state()


if __name__ == "__main__":
    asyncio.run(main())
