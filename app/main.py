from __future__ import annotations

import asyncio
import base64
import json as jsonlib
import logging
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    ChatMessageRequest,
    ContradictionRecord,
    ForkVentureRequest,
    IntakeDraft,
    IntakeDraftRequest,
    MonitorConfigRequest,
    MonitorSchedule,
    ResearchProgress,
    SandboxExperiment,
    SandboxRequest,
    SpecialistReport,
    SubagentEvent,
    SubagentKind,
    SubagentRun,
    SubagentStatus,
    ValidationTask,
    Venture,
    VentureEvent,
    VentureFork,
    VentureIntake,
)
from app.monitor import run_monitor_tick
from app.runtime import get_service, get_subagent_registry
from app.service import VentureService
from app.settings import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


async def _extract_docx_text(data: bytes) -> str:
    """Extract readable text from a .docx (a zip of XML) for text-mode delivery to the model.

    Word documents are not reliably passed as raw bytes in this multimodal path, so we pull the
    paragraphs from the document body. Never raises — a broken document yields an empty string,
    which the caller treats as "no usable text" and the model simply sees the file was attached.
    """
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
        import xml.etree.ElementTree as ET

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ET.fromstring(xml)
        paras = []
        for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [t.text or "" for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
            line = " ".join("".join(texts).split())
            if line:
                paras.append(line)
        return "\n".join(paras)
    except Exception:
        log.exception("Failed to extract .docx text from an attachment (non-fatal)")
        return ""

# ---------------------------------------------------------------------------
# Lifespan: background monitor cron
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the monitor cron task when the server boots; cancel it on shutdown."""
    monitor_task = asyncio.create_task(_monitor_cron_loop())
    log.info(
        "Monitor cron started (wake interval: %ds)", settings.monitor_interval_seconds
    )
    # _adk()'s session service builds its tables lazily, on the FIRST get_session/create_session
    # call — not at construction — so the model client, the async engine, and (on a bare database)
    # a real DDL round-trip all land on whichever request happens to be first. Measured live: that
    # made a plain history read take 15+ seconds on a fresh process, indistinguishable from a hung
    # request until it finally resolved. Absorbing that cost here, once, at boot (harmless on Cloud
    # Run's scale-to-zero — this just becomes part of the cold-start the platform already pays for)
    # means no real founder request is ever the one holding the bill for it.
    try:
        sessions, _ = _adk()
        # Reproduced live: a transient DNS/network blip on the DB host left this await genuinely
        # hanging rather than raising — no exception ever arrived for the `except` below to catch,
        # so the whole server sat at "Waiting for application startup" indefinitely even after the
        # network recovered, with no request of any kind (not even /healthz) able to get through.
        # A hard timeout turns "the whole server is dead until someone notices and restarts it"
        # into "this one optimization didn't pay off this boot" — the first real request pays the
        # cold-start cost instead, exactly like before this warm-up existed.
        await asyncio.wait_for(
            sessions.get_session(app_name=_ADK_APP_NAME, user_id="_warmup", session_id="_warmup"),
            timeout=20.0,
        )
    except Exception:
        log.exception("ADK session warm-up failed (non-fatal, continuing startup)")

    # Backgrounded, not awaited here: get_subagent_registry() does its own real, synchronous
    # Postgres connection-pool setup on first call — the same cold-start cost shape as the ADK
    # warm-up above, but unlike that one this must never sit on startup's own critical path.
    # Verified live: awaiting it inline here made every short-lived TestClient-based test (which
    # re-runs this whole lifespan on every instantiation) pay that cost on its own, repeatedly —
    # the founder never sees this path at all, so there is no first-request cost being "absorbed"
    # by doing it eagerly the way there is for the ADK warm-up.
    subagent_boot_task = asyncio.create_task(_boot_subagent_registry())

    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        log.info("Monitor cron stopped")
        subagent_boot_task.cancel()
        try:
            await subagent_boot_task
        except asyncio.CancelledError:
            pass


async def _boot_subagent_registry() -> None:
    """Wires subagent completion → the founder's chat session (see _wake_main_agent below) and
    recovers any run orphaned by a previous process's crash. See the cancellation/backgrounding
    note at its call site in lifespan()."""
    try:
        registry = await asyncio.to_thread(get_subagent_registry)
        registry.set_wake_hook(lambda run: asyncio.create_task(_wake_main_agent(run)))
        recovered = await registry.recover_stale_on_boot()
        if recovered:
            log.info(
                "Recovered %d stale subagent run(s) orphaned by the previous process", len(recovered)
            )
    except Exception:
        log.exception("Subagent registry boot recovery failed (non-fatal, continuing)")


async def _monitor_cron_loop() -> None:
    """Wake up every monitor_interval_seconds, tick every enabled schedule that is due."""
    while True:
        await asyncio.sleep(settings.monitor_interval_seconds)
        await _run_all_monitor_ticks()


async def _run_all_monitor_ticks() -> None:
    service = get_service()
    schedules = service.state.list_monitor_schedules()
    if not schedules:
        return
    log.info("Monitor cron: checking %d schedule(s)", len(schedules))
    for schedule in schedules:
        if not schedule.enabled:
            continue
        try:
            # Run in the default executor so blocking DB/HTTP work doesn't block the event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, run_monitor_tick, service, schedule.venture_id
            )
        except Exception as exc:
            log.error(
                "Monitor cron: error ticking venture %s: %s",
                schedule.venture_id,
                exc,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cogen Venture Twin",
    version="0.3.0",
    description=(
        "Persistent adversarial venture underwriting, experimentation, "
        "execution agent and long-horizon monitor."
    ),
    lifespan=lifespan,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def service_dep() -> VentureService:
    return get_service()


# ---------------------------------------------------------------------------
# Agent chat — the same ADK-tooled agent from app/agent.py, reached from the
# web product instead of only the ADK playground. The model never edits state
# directly; every consequential action still goes through the typed tools, so
# this is a second front door onto the same guarantees, not a new surface.
# ---------------------------------------------------------------------------

_ADK_APP_NAME = "cogen-web"
_adk_sessions = None
_adk_runner = None


def _adk_session_db_url() -> str:
    """Derive the async SQLAlchemy URL the agent's conversation history is stored under.

    Postgres reuses the same database as everything else (ADK creates its own tables there).
    SQLite gets a dedicated file rather than sharing the app's own sqlite3 file, so the async
    SQLAlchemy engine here never contends with the app's synchronous connections over one file.
    """
    settings = get_settings()
    backend = settings.database_backend.lower()
    if backend in {"postgres", "postgresql", "neon"}:
        url = settings.database_url or ""
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix):]
                break
        else:
            return url
        # asyncpg speaks TLS directly and its connect() rejects libpq-only query params
        # (sslmode, channel_binding) that providers like Neon put on the URL for psycopg —
        # passing them through crashes every connection attempt, so translate/drop instead.
        split = urlsplit(url)
        params = dict(parse_qsl(split.query))
        sslmode = params.pop("sslmode", None)
        params.pop("channel_binding", None)
        if sslmode and sslmode != "disable":
            # asyncpg's own `ssl` kwarg accepts the same libpq mode names sslmode does
            # (require/prefer/verify-full/...) — carry the value through unchanged.
            params["ssl"] = sslmode
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(params), split.fragment))
    sqlite_path = Path(settings.sqlite_path)
    sessions_path = sqlite_path.with_name(f"{sqlite_path.stem}_sessions{sqlite_path.suffix or '.db'}")
    return f"sqlite+aiosqlite:///{sessions_path.as_posix()}"


def _adk() -> tuple:
    """Lazily construct the ADK session service/runner.

    Deferred past import time so a test run that never calls the chat endpoint never has to
    construct a live model client. Backed by the database, not memory — a conversation with the
    agent must survive a process restart (Cloud Run scales to zero constantly) the same way the
    venture data it talks about does. Verified across two separate process invocations against
    the same file: a session written in one process is fully readable, events included, in a
    fresh process with no shared memory.
    """
    global _adk_sessions, _adk_runner
    if _adk_runner is None:
        from google.adk.runners import Runner
        from google.adk.sessions import DatabaseSessionService

        from app.agent import root_agent

        _adk_sessions = DatabaseSessionService(db_url=_adk_session_db_url())
        _adk_runner = Runner(agent=root_agent, app_name=_ADK_APP_NAME, session_service=_adk_sessions)
    return _adk_sessions, _adk_runner


def _sse(event: dict) -> str:
    return f"data: {jsonlib.dumps(event, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Subagent wake-up: when a sandbox/specialist SubagentRun finishes, the founder's chat agent
# reports back on its own instead of waiting to be asked. Per-venture queues let an open chat tab
# see this live; the turn is always driven through the same durable ADK session either way, so it
# shows up on the next /agent/history load even if nobody was watching when it happened.
# ---------------------------------------------------------------------------

_agent_subscribers: dict[str, set[asyncio.Queue]] = {}
_analysis_tasks: set[asyncio.Task] = set()
_analysis_threads: set[threading.Thread] = set()


def _run_analysis_background(service: VentureService, job_id: str) -> None:
    try:
        service.run_analysis_job(job_id)
    except Exception:
        log.exception("Background analysis job %s failed", job_id)
    finally:
        _analysis_threads.discard(threading.current_thread())


def _schedule_analysis(service: VentureService, job_id: str) -> None:
    """Start durable analysis without making the initiating HTTP request wait for it."""
    # Normal ASGI requests have a running loop.  Starlette's synchronous TestClient (and other
    # embedders that call this sync endpoint from a worker thread) do not; calling create_task
    # there raises before the 202 job response can be returned.  The durable job is already the
    # source of truth, so a daemon thread is the correct fallback for those callers.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        thread = threading.Thread(
            target=_run_analysis_background,
            args=(service, job_id),
            name=f"venture-analysis-{job_id}",
            daemon=True,
        )
        _analysis_threads.add(thread)
        thread.start()
        return

    task = loop.create_task(asyncio.to_thread(service.run_analysis_job, job_id))
    _analysis_tasks.add(task)

    def finish(completed: asyncio.Task) -> None:
        _analysis_tasks.discard(completed)
        try:
            completed.result()
        except Exception:
            log.exception("Background analysis job %s failed", job_id)

    task.add_done_callback(finish)


def _publish_agent_event(venture_id: str, event: dict) -> None:
    for queue in _agent_subscribers.get(venture_id, ()):
        queue.put_nowait(event)


async def _wake_main_agent(run: SubagentRun) -> None:
    """Drive one more turn through the subagent's parent chat session so the agent reports the
    result itself, the way a competent junior returning from a task would — not something the
    founder has to come back and ask about. Runs unconditionally against the durable
    DatabaseSessionService session, so the turn persists (and /agent/history replays it) even if
    the founder navigated away; also broadcast live to anyone with /agent/subscribe open."""
    if not run.parent_session_id:
        return
    venture_id = run.venture_id
    kind_label = "sandbox scenario" if run.kind == SubagentKind.SANDBOX else "specialist research"
    if run.status == SubagentStatus.SUCCEEDED:
        outcome = f"finished — result: {jsonlib.dumps(run.result_payload, default=str)}"
        instruction = "Report this to the founder now, in your usual voice, and continue the conversation."
    else:
        outcome = f"failed — {run.error}"
        instruction = "Tell the founder plainly that it failed and what you'll try next."
    system_note = f"[Cogen system: background {kind_label} {outcome}. {instruction}]"

    try:
        from google.genai import types as genai_types

        sessions, runner = _adk()
        session = await sessions.get_session(
            app_name=_ADK_APP_NAME, user_id="founder", session_id=run.parent_session_id,
        )
        if session is None:
            return  # no chat session exists for this venture yet — nothing to wake
        content = genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=system_note)])
        async for event in runner.run_async(
            user_id="founder", session_id=run.parent_session_id, new_message=content,
        ):
            for call in event.get_function_calls() or []:
                _publish_agent_event(venture_id, {"type": "tool_call", "name": call.name, "args": call.args or {}})
            for resp in event.get_function_responses() or []:
                payload = resp.response if isinstance(resp.response, dict) else {}
                failed = any("error" in str(k).lower() for k in payload)
                _publish_agent_event(venture_id, {"type": "tool_error" if failed else "tool_result", "name": resp.name})
            if event.content and event.content.parts:
                spoken = "".join(part.text for part in event.content.parts if getattr(part, "text", None))
                if spoken.strip():
                    kind = "final" if event.is_final_response() else "text"
                    _publish_agent_event(venture_id, {"type": kind, "text": spoken})
        _publish_agent_event(venture_id, {"type": "done"})
    except Exception:
        log.exception("Waking the main agent for subagent run %s failed", run.id)
    finally:
        run.woken = True
        get_subagent_registry().state.save_subagent_run(run)


# Reproduced live, twice: the model's response for a whole turn is sometimes a single sentence
# announcing that it is about to do the work ("Starting the full five-specialist research pass...")
# with zero tool calls anywhere in that round — ADK still marks it is_final_response()=True, so
# nothing here catches it as an error and ADK's own instructions against this ("NEVER DESCRIBE
# UNFINISHED WORK AS IN PROGRESS") are not enough to reliably stop a cheap model from doing it
# anyway. A founder sees a confident-sounding sentence and nothing else — no evidence, no findings,
# turn over in under 10 seconds. Narrow on purpose: matches only the specific "I'm about to act"
# progressive-tense pattern actually observed, on a short response, so a genuine longer answer that
# happens to open with a similar word is not misclassified.
_ANNOUNCED_NO_ACTION_RE = re.compile(
    r"^(i(’|'| a)?m\s+|i\s+will\s+now\s+|i\s+am\s+about\s+to\s+)?"
    r"(starting|beginning|kicking off|about to start|going to start|will now start|now starting)\b",
    re.IGNORECASE,
)


def _is_connection_error(exc: BaseException) -> bool:
    """True for a transient DB/network failure — worth retrying hard for, free of LLM cost — as
    opposed to a model-content problem, where retrying harder just spends more tokens on the same
    kind of response. Reproduced live: a DNS blip on the DB host raised asyncpg's
    PostgresConnectionError; OSError also catches socket.gaierror and plain connection resets
    directly, without an asyncpg/sqlalchemy import at module load time gating an offline test run
    that never touches Postgres.
    """
    if isinstance(exc, (OSError, TimeoutError)):
        return True
    try:
        import asyncpg
        if isinstance(exc, asyncpg.exceptions.PostgresConnectionError):
            return True
    except ImportError:
        pass
    try:
        from sqlalchemy.exc import DBAPIError, OperationalError
        if isinstance(exc, (DBAPIError, OperationalError)):
            return True
    except ImportError:
        pass
    return False


def _ground_truth(service: VentureService, venture_id: str) -> str:
    """A short, verified status line read straight from the repository/state store — never the
    flaky ADK chat-session layer — so a retry after a connection failure hands the model FACTS
    instead of asking it to guess whether its own last tool call actually took effect. The chat
    session and the venture data live behind separate connection pools: a session-store write can
    fail while the underlying tool (run_specialist_research, add_founder_evidence, ...) already
    succeeded and durably saved — reproduced live, where a DNS blip broke the chat turn but the
    full 5-specialist research pass had already completed and was sitting on the venture untouched.
    """
    try:
        venture = service.get_venture(venture_id)
        progress = service.research_progress(venture_id)
        parts = [f"{len(venture.evidence)} evidence record(s) on file"]
        if venture.underwriting:
            parts.append(f"decision={venture.underwriting.decision.value}")
        if progress.status not in ("none", "queued"):
            done = len(progress.specialists_done)
            total = len(progress.specialists_total)
            parts.append(f"specialist research: {progress.status} (phase={progress.phase}, {done}/{total} specialists done)")
        return "; ".join(parts)
    except Exception:
        return "current venture state could not be verified"


@app.post("/api/ventures/{venture_id}/agent/message")
async def agent_message(
    venture_id: str,
    request: ChatMessageRequest,
    service: VentureService = Depends(service_dep),
):
    """Stream one turn of the venture-scoped agent conversation as server-sent events.

    Event shapes: {"type": "tool_call", "name", "args"} — {"type": "tool_result", "name"} —
    {"type": "text"|"final", "text"} — {"type": "error", "message"} — {"type": "done"}.
    """
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc

    from google.genai import types as genai_types

    session_id = f"venture:{venture_id}"
    user_id = "founder"

    async def stream():
        sessions, runner = _adk()
        session = await sessions.get_session(app_name=_ADK_APP_NAME, user_id=user_id, session_id=session_id)
        if session is None:
            await sessions.create_session(app_name=_ADK_APP_NAME, user_id=user_id, session_id=session_id)
        # Always grounded, not just on the session's first message: create_session above runs before
        # any model call, so if that very first turn is ever interrupted (a network blip, a page
        # reload, a client timeout — reproduced live via a cancelled curl request), the session row
        # already exists on retry and this context would otherwise never be delivered again for the
        # rest of that session's life, silently leaving every later message with no venture_id at
        # all. Verified live: exactly this caused the agent to treat "give me a status update" as a
        # brand-new, unrelated venture description. A short prefix on every turn costs nothing and
        # removes the fragile "was this really the first message" tracking entirely.
        spoken = request.message.strip() or (
            "[The founder attached an image with no message. Describe what's relevant to this "
            "venture and ask what they'd like you to do with it.]"
        )
        text = (
            f'[Cogen context: the current venture_id is "{venture_id}". Use it for tool calls '
            f"unless the founder names a different venture.]\n\n{spoken}"
        )

        # Verified live: a turn can fail after real, useful work already happened (inspect_venture,
        # run_underwriting, and a real search_web call all succeeded, then the final synthesis step
        # came back empty) and the founder-facing chat had no recovery at all — it just showed an
        # error and stopped, while the *specialist* research path (app/agentic_research.py) already
        # got retry+backoff for the exact same underlying failure class. That inconsistency is the
        # bug: the primary surface people actually use had the weaker guarantee. Retrying here does
        # NOT restart the turn from scratch — it sends a silent "continue" into the SAME session, so
        # the model picks up from tool results already in context instead of repeating a search_web
        # call (or a run_underwriting call) that already succeeded.
        MAX_ROUNDS = 3
        # A connection failure (DNS blip, dropped pool connection) costs nothing to retry — no LLM
        # call was even reached — so it gets a separate, more generous budget than a genuine
        # model-content problem, where retrying more just spends more tokens on the same behavior.
        # Reproduced live: a DNS blip failed two rounds back-to-back with no gap between attempts;
        # this budget plus the growing backoff below gives a real transient outage room to clear
        # within one request instead of asking the founder to notice and retry it themselves.
        MAX_CONNECTION_RETRIES = 6
        parts = [genai_types.Part.from_text(text=text)]
        for attachment in request.attachments:
            mime = attachment.mime_type
            # .docx has no reliable multimodal-by-bytes handling in this path, so extract its text
            # and deliver it as a text part the model can actually read. PDFs and images go straight
            # to the model as bytes — Gemini is multimodal and reads both natively.
            if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                doc_text = await _extract_docx_text(base64.b64decode(attachment.data))
                if doc_text:
                    parts.append(genai_types.Part.from_text(
                        text=f"[Attached document '{attachment.name or 'brief.docx'}']\n{doc_text}"
                    ))
                continue
            parts.append(genai_types.Part.from_bytes(
                data=base64.b64decode(attachment.data), mime_type=mime,
            ))
        message = genai_types.Content(role="user", parts=parts)
        continue_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(
                text="Your last response did not come through. Continue exactly where you left off "
                "using what you already found — do not repeat a tool call you already made "
                "successfully this conversation."
            )],
        )

        round_idx = 0
        model_rounds_used = 0
        connection_retries_used = 0
        tool_failures: set[str] = set()
        while True:
            round_idx += 1
            saw_final_text = False
            made_progress = False
            announced_no_action = False
            round_error: Exception | None = None
            # A tool_call event can stream through before the round dies with no matching
            # tool_result ever arriving — reproduced live: fork_configuration's call was emitted,
            # the round then errored, and on retry the model's own final answer confidently
            # reported the fork as done. No new venture existed. The generic "continue where you
            # left off" framing gave it nothing to distinguish "confirmed" from "sent but unknown,"
            # so it assumed success. Track calls still missing a response by round's end and name
            # them explicitly in the next round's message instead of leaving that to guesswork.
            pending_calls: list[str] = []
            any_tool_call_this_round = False
            final_text: str | None = None
            try:
                async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
                    made_progress = True
                    for call in event.get_function_calls() or []:
                        any_tool_call_this_round = True
                        pending_calls.append(call.name)
                        yield _sse({"type": "tool_call", "name": call.name, "args": call.args or {}})
                    for resp in event.get_function_responses() or []:
                        if resp.name in pending_calls:
                            pending_calls.remove(resp.name)
                        # ADK swallows a tool's exception into the function response content rather
                        # than raising it — verified live: a call missing a required arg came back
                        # through here as an ordinary-looking response, and the model silently
                        # retried. Surface that distinction instead of showing every response as an
                        # identical checkmark.
                        payload = resp.response if isinstance(resp.response, dict) else {}
                        # Check response KEYS only ("error", "status": "error") — never scan value
                        # text, which risks false-flagging a legitimate evidence claim that happens
                        # to mention a word like "error" (e.g. quoting a supplier's error-rate figures).
                        failed = any("error" in str(k).lower() for k in payload)
                        tool_event = {"type": "tool_error" if failed else "tool_result", "name": resp.name}
                        if failed:
                            tool_failures.add(resp.name)
                        else:
                            tool_failures.discard(resp.name)
                        # create_venture/fork_configuration mint a NEW venture id that isn't the one this
                        # chat session is scoped to — without surfacing it, the founder has no way to reach
                        # what was just created except manually hunting the ventures index.
                        # add_founder_evidence/apply_material_change carry a citation the chat log renders
                        # as a source chip on the reply that follows, instead of the model having to
                        # describe sourcing in free text (or, worse, quote the raw assumption_key).
                        # run_sandbox_experiment carries a run_id the chat log turns into a link straight
                        # to that run in the Sandbox tab, instead of the founder having to go find it.
                        # Pass the raw payload through for all five; the client extracts the shape it
                        # expects from whichever key ADK wrapped it in.
                        if not failed and resp.name in {
                            "create_venture", "fork_configuration",
                            "add_founder_evidence", "apply_material_change",
                            "run_specialist_research",
                            "run_sandbox_experiment",
                        }:
                            tool_event["result"] = payload
                        yield _sse(tool_event)
                    if event.content and event.content.parts:
                        spoken = "".join(
                            part.text for part in event.content.parts if getattr(part, "text", None)
                        )
                        if spoken.strip():
                            if event.is_final_response():
                                stripped = spoken.strip()
                                if not any_tool_call_this_round and len(stripped) < 400 \
                                        and _ANNOUNCED_NO_ACTION_RE.match(stripped):
                                    # The whole round's output was one sentence announcing work
                                    # that never happened — show it as narration, not as the
                                    # turn's real answer, and fall through to the retry path
                                    # below instead of ending the turn here.
                                    announced_no_action = True
                                    yield _sse({"type": "text", "text": spoken})
                                else:
                                    # Hold the final until the round is fully observed.  ADK can
                                    # emit a final-looking answer after a tool response containing
                                    # an error; sending it through would let the model claim that a
                                    # failed write/search actually happened.
                                    final_text = spoken
                            else:
                                # A non-final event is a complete, distinct step under this run's
                                # default (non-streaming) RunConfig, not a partial delta of a later
                                # one — safe to show as the model's own brief narration of what it is
                                # about to do, not just a bare tool-call trace with no explanation.
                                yield _sse({"type": "text", "text": spoken})
            except Exception as exc:  # caught per-round so a failed round can still be retried
                round_error = exc
                tool_failures.update(pending_calls)
                log.error(
                    "Agent chat error for venture %s (round %d, model_rounds=%d, "
                    "connection_retries=%d): %s",
                    venture_id, round_idx, model_rounds_used, connection_retries_used, exc, exc_info=True,
                )

            if final_text and not pending_calls and not tool_failures:
                saw_final_text = True
                yield _sse({"type": "final", "text": final_text})
            elif final_text and (pending_calls or tool_failures):
                round_error = round_error or RuntimeError(
                    "The agent produced an answer after a tool failure"
                )

            if saw_final_text:
                break

            is_connection_error = round_error is not None and _is_connection_error(round_error)
            if is_connection_error:
                connection_retries_used += 1
                budget_left = connection_retries_used < MAX_CONNECTION_RETRIES
                attempts_of = MAX_CONNECTION_RETRIES
                attempt_num = connection_retries_used
            else:
                model_rounds_used += 1
                budget_left = model_rounds_used < MAX_ROUNDS
                attempts_of = MAX_ROUNDS
                attempt_num = model_rounds_used

            if budget_left:
                yield _sse({"type": "retry", "attempt": attempt_num + 1, "of": attempts_of})
                # Reproduced live: a DNS blip on the DB host failed round 1, and round 2 fired
                # immediately into the same still-recovering connection and failed the same way —
                # an instant retry cannot tell "the model needs another try" apart from "whatever
                # just broke hasn't cleared yet." A longer, growing pause specifically for
                # connection failures gives a transient outage real room to clear within this one
                # request, instead of surfacing a dead end the founder has to notice and retry.
                if is_connection_error:
                    await asyncio.sleep(3.0 * connection_retries_used)
                elif round_error is not None:
                    await asyncio.sleep(2.0 * model_rounds_used)
                # Only switch to the "continue where you left off" framing once the model actually
                # received the real message and started working — verified live that a round which
                # fails before runner.run_async ever yields a first event never delivers the user's
                # message into the session at all, so "continue" has nothing to continue and the
                # model falls back on unrelated earlier history instead of answering what was asked.
                # Keep resending the real message until some round actually gets through with it.
                if made_progress:
                    # Handed straight to the model, verified against the repository/state store —
                    # never the ADK session layer that just failed — so a retry never has to guess
                    # whether its own last tool call actually took effect. Reproduced live: the
                    # chat session failed mid-turn while the underlying run_specialist_research call
                    # had already completed and saved in full; the model had no way to know that
                    # from its own (now-desynced) context alone.
                    ground_truth = f"SYSTEM NOTE — verified current state: {_ground_truth(service, venture_id)}.\n\n"
                    if announced_no_action:
                        message = genai_types.Content(
                            role="user",
                            parts=[genai_types.Part.from_text(
                                text=ground_truth
                                + "You said you were about to do something but made no tool call at "
                                "all — nothing actually happened. Call the tool now, in this response, "
                                "instead of narrating it again. Do not just restate the same intention."
                            )],
                        )
                    elif pending_calls:
                        unconfirmed = ", ".join(sorted(set(pending_calls)))
                        message = genai_types.Content(
                            role="user",
                            parts=[genai_types.Part.from_text(
                                text=ground_truth
                                + "Your last response did not come through. The state above is "
                                f"verified — use it to tell whether {unconfirmed} already took effect "
                                "instead of guessing or repeating it blindly. Do not describe anything "
                                "as done that the verified state does not actually show. Continue using "
                                "whatever else you already confirmed this conversation."
                            )],
                        )
                    else:
                        message = genai_types.Content(
                            role="user",
                            parts=[genai_types.Part.from_text(
                                text=ground_truth
                                + "Your last response did not come through. Continue exactly where you "
                                "left off using what you already found and the verified state above — "
                                "do not repeat a tool call that state already shows succeeded."
                            )],
                        )
            else:
                # Every attempt this request could afford is spent. The founder is never told to
                # act on this themselves — the verified state below is exactly what the next
                # message (or a reload) will see too, so nothing is lost and nothing needs manual
                # recovery; this is only reached when the connection genuinely never recovered
                # within the retry budget above, which no amount of in-request retrying fixes.
                ground_truth = _ground_truth(service, venture_id)
                if round_error is not None:
                    yield _sse({
                        "type": "error",
                        "message": f"Lost the connection partway through this turn "
                        f"({type(round_error).__name__}) and it did not recover in time. Nothing was "
                        f"lost — verified current state: {ground_truth}.",
                    })
                else:
                    yield _sse({
                        "type": "error",
                        "message": f"No usable response after {attempts_of} attempts. Verified current "
                        f"state: {ground_truth}.",
                    })
                break
        yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


_CONTEXT_PREFIX_RE = re.compile(r'^\[Cogen context:.*?\]\n\n', re.DOTALL)


@app.get("/api/ventures/{venture_id}/agent/history")
async def agent_history(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[dict]:
    """Replay a venture's persisted agent conversation in the same event shapes /agent/message
    streams live, so the frontend renders returning history through the exact same code path
    instead of a second, parallel one.

    The agent's own memory already survives a process restart (DatabaseSessionService) — this is
    what makes the visible transcript survive a page reload too. Verified live: without this, a
    founder who reloads the tab sees a blank chat log even though the agent still remembers
    everything it found, because the rendered history lived only in an in-memory JS array with
    nothing reading the persisted session back.
    """
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc

    sessions, _ = _adk()
    session = await sessions.get_session(
        app_name=_ADK_APP_NAME, user_id="founder", session_id=f"venture:{venture_id}",
    )
    if session is None:
        return []

    items: list[dict] = []
    for event in session.events:
        if event.author == "user":
            parts = event.content.parts if event.content else []
            spoken = "".join(part.text for part in parts if getattr(part, "text", None))
            spoken = _CONTEXT_PREFIX_RE.sub("", spoken, count=1).strip()
            attachments = [
                {
                    "mime_type": part.inline_data.mime_type,
                    "data": base64.b64encode(part.inline_data.data).decode("ascii"),
                }
                for part in parts
                if getattr(part, "inline_data", None) and part.inline_data.data
            ]
            if spoken or attachments:
                entry = {"type": "user", "text": spoken}
                if attachments:
                    entry["attachments"] = attachments
                items.append(entry)
            continue
        for call in event.get_function_calls() or []:
            items.append({"type": "tool_call", "name": call.name, "args": call.args or {}})
        for resp in event.get_function_responses() or []:
            payload = resp.response if isinstance(resp.response, dict) else {}
            failed = any("error" in str(k).lower() for k in payload)
            entry = {"type": "tool_error" if failed else "tool_result", "name": resp.name}
            if not failed and resp.name in {
                "create_venture", "fork_configuration",
                "add_founder_evidence", "apply_material_change",
                "run_specialist_research",
                "run_sandbox_experiment",
            }:
                entry["result"] = payload
            items.append(entry)
        if event.content and event.content.parts:
            spoken = "".join(part.text for part in event.content.parts if getattr(part, "text", None))
            if spoken.strip():
                items.append({"type": "final" if event.is_final_response() else "text", "text": spoken})
    return items


@app.get("/api/ventures/{venture_id}/agent/subscribe")
async def agent_subscribe(
    venture_id: str,
    service: VentureService = Depends(service_dep),
):
    """Long-lived SSE connection an open chat tab holds so a subagent's own wake-up turn (see
    _wake_main_agent) appears live instead of only on the next page load. Same event shapes as
    /agent/message, so the frontend's existing applyAgentEvent handles both unchanged."""
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc

    queue: asyncio.Queue = asyncio.Queue()
    _agent_subscribers.setdefault(venture_id, set()).add(queue)

    async def stream():
        try:
            while True:
                event = await queue.get()
                yield _sse(event)
        finally:
            _agent_subscribers.get(venture_id, set()).discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/ventures/{venture_id}/todos")
def list_agent_todos(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[dict]:
    """The agent's working Copilot-style todo list for this venture (empty if none yet)."""
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return service.get_agent_todos(venture_id)


@app.post("/api/ventures/{venture_id}/todos")
async def update_agent_todos(
    venture_id: str,
    todos: list[dict],
    service: VentureService = Depends(service_dep),
) -> list[dict]:
    """Persist the agent's working todo list for this venture (also used to tick items done)."""
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    cleaned = [
        {"title": str(t.get("title", "")).strip(), "status": "done" if t.get("status") == "done" else "pending"}
        for t in todos
        if str(t.get("title", "")).strip()
    ]
    return service.save_agent_todos(venture_id, cleaned)


@app.get("/api/ventures/{venture_id}/subagents", response_model=list[SubagentRun])
def list_subagents(
    venture_id: str,
    kind: str | None = None,
    service: VentureService = Depends(service_dep),
) -> list[SubagentRun]:
    """Every sandbox/specialist subagent run against this venture, newest first — powers the
    Sandbox tab's live/past-runs list and the specialist checklist's expandable rows."""
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return get_subagent_registry().list_runs(venture_id, kind=SubagentKind(kind) if kind else None)


@app.get(
    "/api/ventures/{venture_id}/subagents/{run_id}/events",
    response_model=list[SubagentEvent],
)
def list_subagent_events(
    venture_id: str,
    run_id: str,
    service: VentureService = Depends(service_dep),
) -> list[SubagentEvent]:
    """One subagent run's full narration, in order — the same tool_call/tool_result/text/final
    shapes the chat log renders, so the frontend can reuse that rendering code unchanged."""
    try:
        service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return get_subagent_registry().list_events(venture_id, run_id)


@app.get("/api/ventures/{venture_id}/research/progress")
async def research_progress(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> ResearchProgress:
    """Live read of the specialist-research workflow the agent is (or was last) running.

    Polled by the chat UI while a run_specialist_research tool call is in flight, so the founder
    sees which of the five specialists have actually finished instead of a bare multi-minute wait
    with no indication anything is happening.
    """
    try:
        return service.research_progress(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc


def not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'"))


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def ready(service: VentureService = Depends(service_dep)) -> dict[str, object]:
    try:
        checks = service.readiness()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database readiness failed: {type(exc).__name__}") from exc
    return {
        "status": "ready",
        "database_backend": settings.database_backend,
        "research_mode": settings.research_mode,
        "research_provider": settings.research_provider,
        "model": (
            settings.openrouter_model
            if settings.research_provider.lower() == "openrouter"
            else settings.gemini_model
        ),
        "google_cloud_project": settings.google_cloud_project,
        "monitor_interval_seconds": settings.monitor_interval_seconds,
        **checks,
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/intake", response_model=IntakeDraft, status_code=status.HTTP_201_CREATED)
def create_intake_draft(
    request: IntakeDraftRequest,
    service: VentureService = Depends(service_dep),
) -> IntakeDraft:
    return service.plan_intake(request)


@app.post("/api/intake/{draft_id}", response_model=IntakeDraft)
def update_intake_draft(
    draft_id: str,
    request: IntakeDraftRequest,
    service: VentureService = Depends(service_dep),
) -> IntakeDraft:
    return service.plan_intake(request, draft_id)


@app.post("/api/ventures", response_model=Venture, status_code=status.HTTP_201_CREATED)
def create_venture(
    intake: VentureIntake,
    service: VentureService = Depends(service_dep),
) -> Venture:
    return service.create_venture(intake)


@app.get("/api/ventures", response_model=list[Venture])
def list_ventures(service: VentureService = Depends(service_dep)) -> list[Venture]:
    return service.list_ventures()


@app.get("/api/ventures/{venture_id}", response_model=Venture)
def get_venture(venture_id: str, service: VentureService = Depends(service_dep)) -> Venture:
    try:
        return service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.get("/api/ventures/{venture_id}/narrative")
async def get_narrative(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> dict[str, str | None]:
    """The Position tab's lead narrative — generated on first read after underwriting changes and
    cached from then on (see VentureService.get_or_generate_narrative). Returns {"narrative": null}
    rather than an error when there's no underwriting yet or synthesis genuinely failed, so the
    tab's existing structured cards are always a safe fallback, never a broken page."""
    try:
        text = await service.get_or_generate_narrative(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return {"narrative": text or None}


@app.delete("/api/ventures/{venture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_venture(venture_id: str, service: VentureService = Depends(service_dep)) -> None:
    try:
        service.delete_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.post(
    "/api/ventures/{venture_id}/analysis",
    response_model=AnalysisJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_analysis(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> AnalysisJob:
    try:
        job = service.create_analysis_job(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    _schedule_analysis(service, job.id)
    return job


@app.post("/api/ventures/{venture_id}/analysis/sync", response_model=Venture)
def run_analysis_sync(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> Venture:
    try:
        job = service.create_analysis_job(venture_id)
        completed = service.run_analysis_job(job.id)
        if completed.status.value not in {"complete"}:
            raise HTTPException(status_code=500, detail=completed.message)
        return service.get_venture(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.get("/api/jobs/{job_id}", response_model=AnalysisJob)
def get_job(job_id: str, service: VentureService = Depends(service_dep)) -> AnalysisJob:
    try:
        return service.get_job(job_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.post("/api/ventures/{venture_id}/evidence", response_model=Venture)
def add_evidence(
    venture_id: str,
    request: AddEvidenceRequest,
    service: VentureService = Depends(service_dep),
) -> Venture:
    try:
        return service.add_evidence(venture_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/ventures/{venture_id}/changes", response_model=Venture)
def apply_change(
    venture_id: str,
    request: ApplyChangeRequest,
    service: VentureService = Depends(service_dep),
) -> Venture:
    try:
        return service.apply_change(venture_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc


@app.post("/api/ventures/{venture_id}/forks", response_model=Venture, status_code=201)
def create_fork(
    venture_id: str,
    request: ForkVentureRequest,
    service: VentureService = Depends(service_dep),
) -> Venture:
    try:
        return service.fork(venture_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc


@app.get("/api/ventures/{venture_id}/forks", response_model=list[VentureFork])
def list_forks(venture_id: str, service: VentureService = Depends(service_dep)) -> list[VentureFork]:
    return service.forks(venture_id)


@app.post("/api/ventures/{venture_id}/sandbox", response_model=SandboxExperiment)
def run_sandbox(
    venture_id: str,
    request: SandboxRequest,
    service: VentureService = Depends(service_dep),
) -> SandboxExperiment:
    try:
        return service.run_sandbox(venture_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc


@app.get("/api/ventures/{venture_id}/sandbox", response_model=list[SandboxExperiment])
def list_sandbox_experiments(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[SandboxExperiment]:
    """Return every sandbox experiment run against this venture, oldest first."""
    return service.experiments(venture_id)


@app.get("/api/ventures/{venture_id}/events", response_model=list[VentureEvent])
def list_events(venture_id: str, service: VentureService = Depends(service_dep)) -> list[VentureEvent]:
    return service.events(venture_id)


@app.get("/api/ventures/{venture_id}/timeline")
def get_timeline(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[dict]:
    """Return the venture event log as a chronological timeline with per-event elapsed-second deltas.

    Each entry contains:
    - ``elapsed_seconds``: seconds since the first event (useful for timing the pipeline)
    - ``event_type``: the event kind
    - ``actor``: who caused it
    - ``occurred_at``: ISO timestamp
    - ``payload``: the event's data (includes ``phase_elapsed_seconds`` for workflow checkpoints)
    """
    try:
        return service.timeline(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.get("/api/ventures/{venture_id}/contradictions", response_model=list[ContradictionRecord])
def list_contradictions(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[ContradictionRecord]:
    return service.contradictions(venture_id)


@app.get("/api/ventures/{venture_id}/specialists", response_model=list[SpecialistReport])
def list_specialists(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[SpecialistReport]:
    return service.specialists(venture_id)


@app.get("/api/ventures/{venture_id}/validation", response_model=list[ValidationTask])
def list_validation_tasks(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[ValidationTask]:
    return service.validation_tasks(venture_id)


@app.post("/api/ventures/{venture_id}/roadmap/{step_id}/complete", response_model=Venture)
def complete_step(
    venture_id: str,
    step_id: str,
    service: VentureService = Depends(service_dep),
) -> Venture:
    try:
        return service.complete_step(venture_id, step_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/ventures/{venture_id}/monitor", response_model=MonitorSchedule, status_code=201)
def configure_monitor(
    venture_id: str,
    request: MonitorConfigRequest,
    service: VentureService = Depends(service_dep),
) -> MonitorSchedule:
    """Enable or configure the periodic evidence-staleness monitor for a venture.

    Set ``interval_hours`` (default 168 = weekly) to control how often the cron checks.
    The schedule is persisted so it survives server restarts.
    """
    try:
        return service.configure_monitor(venture_id, request)
    except KeyError as exc:
        raise not_found(exc) from exc


@app.get("/api/ventures/{venture_id}/monitor", response_model=MonitorSchedule)
def get_monitor(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> MonitorSchedule:
    """Return the monitor schedule for a venture, including staleness info from the last check."""
    schedule = service.get_monitor_schedule(venture_id)
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail="No monitor schedule configured for this venture. POST to /monitor to enable.",
        )
    return schedule


@app.post("/api/ventures/{venture_id}/monitor/tick", response_model=MonitorSchedule)
def force_monitor_tick(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> MonitorSchedule:
    """Force an immediate monitor tick — useful for testing without waiting for the cron interval.

    Bypasses the ``next_due_at`` check; always runs staleness detection.
    """
    from datetime import UTC, datetime
    from app.monitor import MonitorWorker

    # Temporarily wind back next_due_at so the worker considers it due
    schedule = service.get_monitor_schedule(venture_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="No monitor schedule configured.")
    original_due = schedule.next_due_at
    schedule.next_due_at = datetime(2000, 1, 1, tzinfo=UTC)
    service.state.save_monitor_schedule(schedule)
    try:
        result = MonitorWorker().tick(service, venture_id)
    except Exception as exc:
        # Restore the schedule on error
        schedule.next_due_at = original_due
        service.state.save_monitor_schedule(schedule)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Monitor tick returned no result.")
    return result


@app.post("/api/demo", response_model=Venture, status_code=status.HTTP_201_CREATED)
async def create_demo(
    service: VentureService = Depends(service_dep),
) -> Venture:
    venture, job = service.create_demo_venture()
    service.run_analysis_job(job.id)
    return service.get_venture(venture.id)


# ---------------------------------------------------------------------------
# Founder model & weekly recommendations — cross-venture, cross-session memory
# ---------------------------------------------------------------------------


@app.get("/api/founder/model")
def get_founder_model(service: VentureService = Depends(service_dep)) -> dict:
    """The aggregated founder profile learned across all their ventures."""
    return service.founder_model()


@app.get("/api/founder/tailoring")
def get_founder_tailoring(service: VentureService = Depends(service_dep)) -> dict:
    """The founder-model block injected into the agent's context for tailored (not sycophantic)
    responses."""
    return {"context": service.tailoring_context()}


@app.get("/api/ventures/{venture_id}/pivots")
def get_pivot_candidates(
    venture_id: str,
    service: VentureService = Depends(service_dep),
) -> list[dict]:
    """Validated pivot/branch candidates for a rejected/conditional venture, fit to the founder."""
    return service.pivot_candidates(venture_id)


@app.get("/api/founder/recommendations")
def list_recommendations(service: VentureService = Depends(service_dep)) -> list[dict]:
    """All weekly recommendations generated for this founder, newest first."""
    return service.list_recommendations()


@app.post("/api/founder/recommendations")
async def generate_recommendation(service: VentureService = Depends(service_dep)) -> dict:
    """Run the weekly recommendation agent now and persist the result."""
    return await service.generate_recommendation()


@app.get("/api/founder/memory")
def get_user_memory(venture_id: str | None = None, service: VentureService = Depends(service_dep)) -> list[dict]:
    """Durable cross-session user-memory facts (optionally filtered to one venture's contributions)."""
    return service.recall_user_memory(venture_id)
