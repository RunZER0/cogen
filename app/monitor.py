"""Cogen monitor: periodic staleness detection and targeted re-research.

The MonitorWorker is intentionally lightweight — it does not own an analysis pipeline.
It delegates to the existing VentureService and WorkflowRunner so every re-check goes
through the same idempotent, checkpointed workflow that the original analysis used.

Lifecycle
---------
1. ``mark_stale_evidence(venture)`` — walk evidence, flag items that have exceeded
   their staleness window, return the assumption keys that became stale.
2. ``MonitorWorker.tick(service, venture_id)`` — the main cron entry point:
   a. Load venture and schedule.
   b. Mark stale evidence.
   c. If any assumptions are stale AND the schedule is due: queue a targeted workflow
      with an idempotency key that is date-scoped so it runs at most once per interval.
   d. Emit events; update the schedule's next_due_at.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.domain import (
    EventType,
    MonitorConfigRequest,
    MonitorSchedule,
)
from app.service import VentureService

log = logging.getLogger(__name__)


def mark_stale_evidence(venture) -> list[str]:
    """Flag evidence that has exceeded its staleness window.

    Mutates evidence items in-place (sets ``stale=True``).
    Returns the assumption keys whose evidence became stale.

    The venture is NOT saved here — callers decide whether to persist
    after inspecting the stale keys.
    """
    now = datetime.now(UTC)
    stale_keys: set[str] = set()
    for item in venture.evidence:
        if item.stale:
            # Already flagged; still track the assumption key
            if item.assumption_key:
                stale_keys.add(item.assumption_key)
            continue
        if item.is_expired(now):
            item.stale = True
            if item.assumption_key:
                stale_keys.add(item.assumption_key)

    # Mirror staleness onto assumptions so the orchestrator prioritises them
    assumption_map = venture.assumption_map()
    for key in stale_keys:
        assumption = assumption_map.get(key)
        if assumption is not None:
            assumption.stale = True

    return sorted(stale_keys)


class MonitorWorker:
    """Single-method cron worker — call ``tick`` from the lifespan background task."""

    def tick(self, service: VentureService, venture_id: str) -> MonitorSchedule | None:
        """Run one monitor cycle for a venture.

        Returns the updated MonitorSchedule, or None if monitoring is disabled
        or the schedule is not yet due.
        """
        schedule = service.state.get_monitor_schedule(venture_id)
        if schedule is None or not schedule.enabled:
            return schedule

        now = datetime.now(UTC)
        due_at = schedule.next_due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)

        if now < due_at:
            log.debug(
                "Monitor for venture %s not yet due (next: %s)",
                venture_id,
                due_at.isoformat(),
            )
            return schedule

        try:
            venture = service.get_venture(venture_id)
        except KeyError:
            log.warning("Monitor tick: venture %s not found — disabling schedule", venture_id)
            schedule.enabled = False
            schedule.updated_at = now
            return service.state.save_monitor_schedule(schedule)

        # 1. Mark expired evidence stale
        stale_keys = mark_stale_evidence(venture)

        # 2. Persist the updated venture if anything changed
        if stale_keys:
            venture.updated_at = now
            service.repository.save_venture(venture)
            service.state.event(
                venture_id,
                EventType.MONITOR_STALE_DETECTED,
                f"monitor:{venture_id}:{now.date().isoformat()}:stale",
                {
                    "stale_assumption_keys": stale_keys,
                    "check_count": schedule.check_count + 1,
                },
            )
            log.info(
                "Monitor: venture %s has %d stale assumption(s): %s",
                venture_id,
                len(stale_keys),
                stale_keys,
            )
        else:
            log.info("Monitor: venture %s — no stale evidence found", venture_id)

        # 3. If there are stale assumptions, queue a targeted re-research workflow
        recheck_job_id: str | None = None
        if stale_keys:
            iso_date = now.date().isoformat()
            idempotency_key = f"monitor:{venture_id}:{iso_date}"
            try:
                job = service.create_analysis_job(venture_id, idempotency_key=idempotency_key)
                recheck_job_id = job.id
                # Run asynchronously (non-blocking) — the job is queued in the DB;
                # the background task in main.py can pick it up or the next /analysis call will.
                service.state.event(
                    venture_id,
                    EventType.MONITOR_RECHECK_QUEUED,
                    f"monitor:{venture_id}:{iso_date}:recheck",
                    {
                        "job_id": recheck_job_id,
                        "stale_assumption_keys": stale_keys,
                    },
                )
                log.info(
                    "Monitor: queued re-research job %s for venture %s (stale keys: %s)",
                    recheck_job_id,
                    venture_id,
                    stale_keys,
                )
            except Exception as exc:
                log.error(
                    "Monitor: failed to queue re-research for venture %s: %s",
                    venture_id,
                    exc,
                )

        # 4. Update the schedule
        schedule.last_checked_at = now
        schedule.next_due_at = now + timedelta(hours=schedule.interval_hours)
        schedule.stale_assumption_keys = stale_keys
        schedule.check_count += 1
        schedule.updated_at = now
        return service.state.save_monitor_schedule(schedule)


# Module-level singleton used by the FastAPI lifespan background task
_worker = MonitorWorker()


def run_monitor_tick(service: VentureService, venture_id: str) -> MonitorSchedule | None:
    """Public entry point called by the lifespan cron loop."""
    return _worker.tick(service, venture_id)
