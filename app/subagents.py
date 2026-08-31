"""Shared async runtime for standalone subagent runs — sandbox scenarios and specialist research.

Both kinds share one durable run/event schema, and events use the exact same shape the main chat
agent already streams over SSE (tool_call/tool_result/text/final/error — see app/main.py's
/agent/message), so the frontend can render a subagent's live progress with the same code that
already renders the chat log.

Sandbox runs are genuinely concurrent: `launch()` starts each one as an independent asyncio.Task,
so several scenarios can be in flight at once. Specialists go through `run_inline` instead —
WorkflowRunner's own sequential loop deliberately lets each specialist see what its predecessors
confirmed this round (see workflow.py's confirmed_context); running them as independent Tasks
would silently drop that cross-specialist corroboration. run_inline still gives each one the same
durable run/event trail without changing when it executes relative to its siblings.
"""
from __future__ import annotations

import logging
from asyncio import CancelledError, Task, create_task
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain import SubagentEvent, SubagentKind, SubagentRun, SubagentStatus, utc_now
from app.state import StateStore

log = logging.getLogger(__name__)

EmitFn = Callable[..., None]
WakeHook = Callable[[SubagentRun], None]


class SubagentRegistry:
    def __init__(self, state: StateStore):
        self.state = state
        self._tasks: dict[str, Task] = {}
        self._wake_hook: WakeHook | None = None

    def set_wake_hook(self, hook: WakeHook | None) -> None:
        self._wake_hook = hook

    def _emit_for(self, run: SubagentRun) -> EmitFn:
        def emit(event_type: str, **fields: Any) -> None:
            run.event_seq += 1
            self.state.append_subagent_event(
                SubagentEvent(
                    run_id=run.id, venture_id=run.venture_id, seq=run.event_seq,
                    type=event_type, payload=fields,
                )
            )
            # A round checkpoint doubles as the crash-recovery signal for specialists: everything
            # persisted up to here (see orchestration.py's run_role) survives even if the round
            # after this one never finishes, instead of the whole role's work vanishing on retry.
            if event_type == "round_checkpoint" and "round" in fields:
                run.round_index = int(fields["round"])
            run.heartbeat_at = utc_now()
            run.updated_at = run.heartbeat_at
            self.state.save_subagent_run(run)

        return emit

    def _new_run(
        self,
        kind: SubagentKind,
        venture_id: str,
        *,
        workflow_id: str | None,
        role: Any,
        batch_id: str | None,
        parent_session_id: str | None,
        input_payload: dict,
    ) -> SubagentRun:
        run = SubagentRun(
            kind=kind,
            venture_id=venture_id,
            workflow_id=workflow_id,
            role=role,
            batch_id=batch_id,
            parent_session_id=parent_session_id,
            input_payload=input_payload,
        )
        return self.state.save_subagent_run(run)

    async def launch(
        self,
        kind: SubagentKind,
        venture_id: str,
        *,
        work: Callable[[EmitFn], Awaitable[Any]],
        parent_session_id: str | None = None,
        workflow_id: str | None = None,
        role: Any = None,
        batch_id: str | None = None,
        input_payload: dict | None = None,
    ) -> SubagentRun:
        """Dispatch `work(emit)` as a tracked, independently-running asyncio.Task and return
        immediately — the caller (an ADK tool) is never blocked on the work finishing."""
        run = self._new_run(
            kind, venture_id, workflow_id=workflow_id, role=role, batch_id=batch_id,
            parent_session_id=parent_session_id, input_payload=input_payload or {},
        )
        emit = self._emit_for(run)
        task = create_task(self._execute_async(run, work, emit))
        self._tasks[run.id] = task
        return run

    async def _execute_async(
        self, run: SubagentRun, work: Callable[[EmitFn], Awaitable[Any]], emit: EmitFn,
    ) -> None:
        run.status = SubagentStatus.RUNNING
        run.started_at = utc_now()
        run.attempt += 1
        self.state.save_subagent_run(run)
        try:
            result = await work(emit)
            run.status = SubagentStatus.SUCCEEDED
            run.result_payload = result if isinstance(result, dict) else {"result": result}
        except CancelledError:
            run.status = SubagentStatus.CRASHED
            run.error = "Cancelled."
            raise
        except Exception as exc:
            log.exception("Subagent run %s (%s) failed", run.id, run.kind.value)
            run.status = SubagentStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            self.state.save_subagent_run(run)
            self._tasks.pop(run.id, None)
        if self._wake_hook and run.parent_session_id:
            try:
                self._wake_hook(run)
            except Exception:
                log.exception("Wake hook failed for subagent run %s", run.id)

    def run_inline(
        self,
        kind: SubagentKind,
        venture_id: str,
        *,
        work: Callable[[EmitFn], Any],
        workflow_id: str | None = None,
        role: Any = None,
        batch_id: str | None = None,
        input_payload: dict | None = None,
        wrap_emit: Callable[[EmitFn], EmitFn] | None = None,
    ) -> tuple[SubagentRun, Any]:
        """Execute `work(emit)` synchronously on the calling thread — used by specialists, which
        must stay sequential (see module docstring). Re-raises whatever `work` raises, after
        persisting the run as FAILED, so WorkflowRunner's own retry/checkpoint logic is unchanged.

        `wrap_emit`, when given, wraps the run's own event-persisting emit before handing it to
        `work` — lets a caller (WorkflowRunner) layer its own side effect on specific event types
        (folding a round_checkpoint into its ResearchBatch) without this module knowing anything
        about what a caller's events mean."""
        run = self._new_run(
            kind, venture_id, workflow_id=workflow_id, role=role, batch_id=batch_id,
            parent_session_id=None, input_payload=input_payload or {},
        )
        emit = self._emit_for(run)
        if wrap_emit:
            emit = wrap_emit(emit)
        run.status = SubagentStatus.RUNNING
        run.started_at = utc_now()
        run.attempt += 1
        self.state.save_subagent_run(run)
        try:
            result = work(emit)
        except Exception as exc:
            run.status = SubagentStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            self.state.save_subagent_run(run)
            raise
        run.status = SubagentStatus.SUCCEEDED
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        self.state.save_subagent_run(run)
        return run, result

    def get_run(self, run_id: str) -> SubagentRun | None:
        return self.state.get_subagent_run(run_id)

    def list_runs(self, venture_id: str, kind: SubagentKind | None = None) -> list[SubagentRun]:
        return self.state.list_subagent_runs(venture_id, kind=kind)

    def list_events(self, venture_id: str, run_id: str) -> list[SubagentEvent]:
        return self.state.list_subagent_events(venture_id, run_id)

    async def recover_stale_on_boot(self, heartbeat_timeout_s: int = 120) -> list[SubagentRun]:
        """Mark any run still RUNNING past its heartbeat timeout as CRASHED. Called once at
        startup (see app/main.py's lifespan): nothing was legitimately running a moment ago, so
        every such row belongs to a process that is gone. A crashed sandbox run is cheap to
        re-dispatch from scratch on the founder's next ask; a crashed specialist is picked up
        again the next time run_specialist_research runs that workflow — WorkflowRunner's own
        completed-phase/role checkpointing skips whatever genuinely finished, and the round-level
        checkpoint above means a crashed role does not lose earlier rounds' findings either."""
        cutoff = datetime.now(UTC) - timedelta(seconds=heartbeat_timeout_s)
        recovered: list[SubagentRun] = []
        for run in self.state.list_all_subagent_runs():
            if run.status != SubagentStatus.RUNNING:
                continue
            heartbeat = run.heartbeat_at
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            if heartbeat >= cutoff:
                continue
            run.status = SubagentStatus.CRASHED
            run.error = "Process restarted while this run was still in progress."
            run.updated_at = utc_now()
            self.state.save_subagent_run(run)
            recovered.append(run)
        return recovered
