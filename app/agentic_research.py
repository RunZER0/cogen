"""Real ADK multi-agent research: five specialist ADK agents, one per role, each with live web
tools (search_web, browse_page_for_details) and its own model instance and instruction — not one
model role-playing five personas through a Python-level prompt swap, which is what the single-
completion providers in app/research.py do.

This is a drop-in ResearchProvider: it returns the exact same list[ResearchFinding] shape as
GeminiGroundedResearchProvider/OpenRouterGroundedResearchProvider, so SpecialistOrchestrator,
EvidenceLedger's admissibility gate, and WorkflowRunner's per-specialist checkpointing are completely
unchanged. A specialist's proposed findings still only enter the Venture Twin through the same
deterministic policy layer, regardless of whether a single completion produced them or a multi-step
ADK agent's tool-using turn did — the agent proposes, the ledger still decides what is admitted.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.context import WorkingContextBuilder
from app.domain import Confidence, EvidenceType, SpecialistRole, Venture
from app.research import EmitFn, ResearchFinding, ResearchProvider
from app.settings import Settings
from app.source_router import policy_for

_RESPONSE_CONTRACT = """
Your final message must be raw JSON only — no markdown code fence (no ``` before or after it), no prose
before or after it, nothing but the JSON object itself.
Return ONLY a JSON object with a "findings" array. Every finding must contain assumption_key, claim, value
(number or null), unit, evidence_type (official|quote|listing|review|benchmark|observed|founder|model),
confidence (low|medium|high|verified), source_title, source_url, and notes. source_url must be a URL you
actually retrieved this turn via browse_page_for_details — never a URL you only saw in a search snippet or
invented. Use existing assumption keys when applicable; new regulatory/execution keys must start with
regulatory_ or execution_. Monetary units must use the Venture Twin's own currency. If a material value
cannot be established from what you actually retrieved, omit it or set value to null and say why in notes.
""".strip()

_APP_NAME = "cogen-research"


class AgenticSpecialistResearchProvider(ResearchProvider):
    """Five ADK LlmAgents (finance, market, regulatory, execution, adversary) with real search/browse
    tools, replacing single-completion-over-static-snippets research with genuine multi-step agentic
    research per role. Enabled via SPECIALIST_MODE=agentic; the prior one-shot providers remain the
    default (SPECIALIST_MODE=orchestrated) so this is a reversible, not a destructive, change."""

    def __init__(self, settings: Settings, *, context_builder: WorkingContextBuilder | None = None):
        self.settings = settings
        self.context_builder = context_builder or WorkingContextBuilder()
        self._agents: dict[SpecialistRole, Any] = {}
        self._sessions = None

    def _agent_for(self, role: SpecialistRole):
        if role not in self._agents:
            from google.adk.agents import Agent

            from app.agent_tools import as_async_tool, browse_page_for_details, search_web
            from app.llm import build_agent_model
            from app.orchestration import MANDATES

            policy = policy_for(role)
            official_rule = (
                "Regulatory findings are admissible only when the cited URL is a primary official authority "
                "domain (a .gov-style domain, a go.<country-code> domain, or a named national administration "
                "domain). Never label a legal guide, consultancy, blog, marketplace, or directory as official."
                if policy.official_required
                else "Prefer primary, current sources you actually retrieved over summaries."
            )
            instruction = f"""
You are the {role.value} specialist inside Cogen, a persistent venture-underwriting partner that stress-tests
every assumption before the founder risks real capital on it.
Your mandate: {MANDATES[role]}

You do not own venture state — you propose candidate evidence; a deterministic policy layer outside this
conversation decides what is actually admitted. Use search_web to find candidates, then
browse_page_for_details on the most promising ones — a search snippet alone is not evidence, read the
actual page before citing it.

SOURCE POLICY: {', '.join(policy.preferred_sources)}.
{official_rule}
Respect the venture's explicit country, subdivision/locality and currency exactly as given in the working
context below. Never import a law, tax, licence, fee, price, wage norm or currency from a different
jurisdiction merely because it is easier to find.

Attack the business case before supporting it. Do not invent a fee, law, licence, supplier, professional,
price, review, statistic, market size, or URL.

{_RESPONSE_CONTRACT}
""".strip()
            self._agents[role] = Agent(
                name=f"{role.value}_specialist",
                model=build_agent_model(self.settings),
                instruction=instruction,
                tools=[as_async_tool(search_web), as_async_tool(browse_page_for_details)],
            )
        return self._agents[role]

    def _sessions_service(self):
        if self._sessions is None:
            from google.adk.sessions import InMemorySessionService

            # Each call is one bounded, stateless research round — the existing max_rounds design
            # already treats every round as a fresh call with an updated mandate, not a continued
            # conversation, so a fresh in-memory session per call is the correct scope; the durable
            # DatabaseSessionService is reserved for the founder-facing chat, which is a real
            # multi-turn relationship the specialists are not.
            self._sessions = InMemorySessionService()
        return self._sessions

    # Verified live, reproduced independently on two different specialist roles/sessions: ADK's
    # OpenRouter adapter (google/adk/labs/openai/_openai_llm.py) sometimes dereferences
    # response.choices[0] when OpenRouter returns choices: null, raising a bare TypeError with no
    # retry of its own. This is a real, recurring failure in a third-party dependency, not a
    # deterministic one — a fresh attempt with a fresh session has succeeded before. Retrying at
    # this layer is the correct mitigation: we cannot safely patch installed package internals from
    # here, and the existing GeminiModelRouter (app/model_runtime.py) already establishes retry as
    # this codebase's answer to a flaky model layer rather than silently swallowing the failure.
    _MAX_ATTEMPTS = 3
    # Verified live: three attempts fired back-to-back all hit the same failure within one short
    # burst of calls to this model — consistent with transient provider-side rate limiting, which
    # a bare immediate retry does not help and can even worsen. A short, increasing delay gives
    # that condition time to clear, same intent as the backoff already used for the DB/HTTP retries
    # elsewhere in this codebase's provider layer.
    _RETRY_BACKOFF_SECONDS = 3.0

    def research(
        self,
        venture: Venture,
        *,
        role: SpecialistRole | None = None,
        mandate: str | None = None,
        emit: EmitFn | None = None,
    ) -> list[ResearchFinding]:
        import time

        role = role or SpecialistRole.ADVERSARY
        working_context = self.context_builder.build(
            venture, role, mandate or "find evidence that materially changes the launch decision"
        )
        last_error: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            if attempt > 1:
                if emit:
                    emit("retrying", attempt=attempt, of=self._MAX_ATTEMPTS)
                time.sleep(self._RETRY_BACKOFF_SECONDS * (attempt - 1))
            try:
                return asyncio.run(self._research_async(venture, role, working_context, attempt, emit))
            except Exception as exc:  # the ADK/OpenRouter adapter bug surfaces as a bare TypeError
                last_error = exc
        raise RuntimeError(
            f"{role.value} specialist failed after {self._MAX_ATTEMPTS} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    async def _research_async(
        self,
        venture: Venture,
        role: SpecialistRole,
        working_context: str,
        attempt: int,
        emit: EmitFn | None = None,
    ) -> list[ResearchFinding]:
        from google.adk.runners import Runner
        from google.genai import types as genai_types

        agent = self._agent_for(role)
        sessions = self._sessions_service()
        # A fresh session id per attempt — reusing a session that failed mid-turn would replay a
        # partial, possibly-corrupt turn into the retry rather than starting clean.
        session_id = f"{venture.id}:{role.value}:{abs(hash(working_context)) % 1_000_000}:{attempt}"
        await sessions.create_session(app_name=_APP_NAME, user_id="specialist", session_id=session_id)
        runner = Runner(agent=agent, app_name=_APP_NAME, session_service=sessions)

        content = genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=working_context)])
        final_text = ""
        grounded_urls: set[str] = set()
        # Every intermediate ADK event (a tool call, its result, the model's own narration between
        # calls) used to be read only for grounded-URL bookkeeping and then discarded — this is
        # what makes a specialist's live progress inspectable instead of just its final findings.
        async for event in runner.run_async(user_id="specialist", session_id=session_id, new_message=content):
            for call in event.get_function_calls() or []:
                if call.name == "browse_page_for_details" and call.args and call.args.get("url"):
                    grounded_urls.add(str(call.args["url"]))
                if emit:
                    emit("tool_call", name=call.name, args=call.args or {})
            if emit:
                for resp in event.get_function_responses() or []:
                    emit("tool_result", name=resp.name)
            if event.content and event.content.parts:
                spoken = "".join(part.text for part in event.content.parts if getattr(part, "text", None))
                if spoken.strip() and emit:
                    emit("final" if event.is_final_response() else "text", text=spoken)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(part.text for part in event.content.parts if getattr(part, "text", None))

        # Raising (rather than the always-safe _parse) path: a non-empty response that fails to
        # parse is a truncated or malformed turn worth retrying with a fresh session, not a silent
        # zero-findings result from what may have been a fully successful piece of research.
        payload = self._load_payload(final_text)
        if payload is None:
            return []
        return self._build_findings(payload, role, grounded_urls)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Strip a ```json ... ``` (or bare ```) wrapper.

        Verified live: unlike the single-completion providers in app/research.py, which force
        response_format/response_mime_type=json at the API level, an ADK tool-calling agent's final
        text has no such constraint — this model wrapped a perfectly well-formed finding (a real
        Texas Comptroller sales-tax rate, from a page it actually browsed) in a markdown code fence,
        which silently failed json.loads and produced zero findings from a fully successful turn.
        """
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped[3:]
            if stripped.lower().startswith("json"):
                stripped = stripped[4:]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
        return stripped.strip()

    @staticmethod
    def _load_payload(text: str) -> list | None:
        """Parse the model's final text into a list of raw finding dicts.

        Returns None when there was genuinely nothing to say (empty text) — a legitimate, non-
        retriable outcome. Raises ValueError when there IS text but it is not valid JSON after
        fence-stripping — this is the case worth retrying (a truncated or malformed response), and
        letting it raise lets research()'s existing retry-on-exception loop handle it, rather than
        silently returning zero findings from what may have been a fully successful research turn.
        """
        if not text.strip():
            return None
        stripped = AgenticSpecialistResearchProvider._strip_code_fence(text)
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"could not parse JSON from specialist response: {exc}") from exc
        if isinstance(payload, dict):
            payload = payload.get("findings", [])
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _build_findings(
        payload: list, role: SpecialistRole, grounded_urls: set[str]
    ) -> list[ResearchFinding]:
        findings: list[ResearchFinding] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            source_url = str(raw.get("source_url") or "").strip() or None
            # Only trust a URL the specialist actually retrieved via browse_page_for_details this
            # turn (the same "grounded URL" discipline app/research.py already applies) — a URL that
            # only appears in the model's own text was never verified to be real.
            if source_url and source_url not in grounded_urls:
                source_url = None
            try:
                evidence_type = EvidenceType(str(raw.get("evidence_type") or "model").lower())
            except ValueError:
                evidence_type = EvidenceType.MODEL
            try:
                confidence = Confidence(str(raw.get("confidence") or "low").lower())
            except ValueError:
                confidence = Confidence.LOW
            if not source_url and confidence in (Confidence.HIGH, Confidence.VERIFIED):
                confidence = Confidence.LOW
            value = raw.get("value")
            try:
                value = None if value is None else float(value)
            except (TypeError, ValueError):
                value = None
            findings.append(
                ResearchFinding(
                    assumption_key=str(raw.get("assumption_key", "")).strip(),
                    claim=str(raw.get("claim", "")).strip(),
                    value=value,
                    unit=raw.get("unit"),
                    evidence_type=evidence_type,
                    confidence=confidence,
                    source_title=str(raw.get("source_title") or f"{role.value} specialist"),
                    source_url=source_url,
                    notes=raw.get("notes"),
                    role=role,
                )
            )
        return [f for f in findings if f.assumption_key and f.claim]

    @staticmethod
    def _parse(text: str, role: SpecialistRole, grounded_urls: set[str]) -> list[ResearchFinding]:
        """Pure, always-safe wrapper: never raises, always returns a list. Used directly by tests
        and anywhere the caller has no retry loop of its own to hand a ValueError to."""
        try:
            payload = AgenticSpecialistResearchProvider._load_payload(text)
        except ValueError:
            return []
        if payload is None:
            return []
        return AgenticSpecialistResearchProvider._build_findings(payload, role, grounded_urls)

    def runtime_health(self) -> dict[str, object]:
        return {"provider": type(self).__name__, "status": "ok", "mode": "agentic-adk-subagents"}
