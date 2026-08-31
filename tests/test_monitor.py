"""Tests for the monitor worker: staleness detection, schedule management, re-research queuing."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    Confidence,
    Evidence,
    EvidenceType,
    MonitorConfigRequest,
    MonitorSchedule,
    VentureIntake,
)
from app.monitor import MonitorWorker, mark_stale_evidence
from app.repository import SQLiteRepository
from app.research import OfflineResearchProvider
from app.service import VentureService


def _make_service(tmp_path) -> VentureService:
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    provider = OfflineResearchProvider()
    return VentureService(repo, provider)


def _supermarket_intake() -> VentureIntake:
    return VentureIntake.model_validate(
        {
            "idea": "neighbourhood supermarket",
            "business_type": "supermarket retail",
            "location": "Nairobi, Kenya",
            "country": "Kenya",
            "currency": "KES",
            "founder": {
                "available_capital": 1_800_000,
                "protected_reserve": 150_000,
                "target_monthly_owner_income": 80_000,
            },
        }
    )


def _old_evidence(assumption_key: str, evidence_type: EvidenceType, days_old: int) -> Evidence:
    """Create evidence backdated by `days_old` days."""
    observed = datetime.now(UTC) - timedelta(days=days_old)
    return Evidence(
        assumption_key=assumption_key,
        claim=f"Test claim for {assumption_key}",
        value=1000.0,
        unit="KES",
        evidence_type=evidence_type,
        confidence=Confidence.MEDIUM,
        source_title="Test source",
        observed_at=observed,
        accessed_at=observed,
    )


# ---------------------------------------------------------------------------
# Evidence.is_expired
# ---------------------------------------------------------------------------

class TestEvidenceExpiry:
    def test_fresh_evidence_not_expired(self):
        e = _old_evidence("setup_costs", EvidenceType.QUOTE, 10)
        assert not e.is_expired()

    def test_old_official_evidence_expired(self):
        # official default = 90 days
        e = _old_evidence("regulatory_registration_path", EvidenceType.OFFICIAL, 91)
        assert e.is_expired()

    def test_official_not_expired_at_89_days(self):
        e = _old_evidence("regulatory_registration_path", EvidenceType.OFFICIAL, 89)
        assert not e.is_expired()

    def test_model_evidence_expires_after_30_days(self):
        e = _old_evidence("setup_costs", EvidenceType.MODEL, 31)
        assert e.is_expired()

    def test_custom_stale_after_days_overrides_default(self):
        e = _old_evidence("setup_costs", EvidenceType.OFFICIAL, 10)
        e = e.model_copy(update={"stale_after_days": 5})
        assert e.is_expired()  # custom 5-day window

    def test_already_stale_evidence_returns_expired(self):
        e = _old_evidence("setup_costs", EvidenceType.QUOTE, 5)
        e = e.model_copy(update={"stale": True})
        # is_expired checks age, not the stale flag
        assert not e.is_expired()  # only 5 days old vs 180-day quote default


# ---------------------------------------------------------------------------
# mark_stale_evidence
# ---------------------------------------------------------------------------

class TestMarkStaleEvidence:
    def test_no_evidence_returns_empty(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())
        service.run_analysis_job(service.create_analysis_job(venture.id).id)
        venture = service.get_venture(venture.id)
        # Clear evidence to test edge case
        venture.evidence = []
        stale_keys = mark_stale_evidence(venture)
        assert stale_keys == []

    def test_fresh_evidence_not_flagged(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())
        service.run_analysis_job(service.create_analysis_job(venture.id).id)
        venture = service.get_venture(venture.id)
        # All evidence just got added — should not be stale
        stale_keys = mark_stale_evidence(venture)
        # Fresh offline demo evidence (default 730-day window) should not flag
        assert len(stale_keys) == 0

    def test_old_official_evidence_flagged(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())
        # Plant an old official evidence item directly
        old_e = _old_evidence("regulatory_registration_path", EvidenceType.OFFICIAL, 91)
        venture.evidence.append(old_e)
        service.repository.save_venture(venture)

        stale_keys = mark_stale_evidence(venture)
        assert "regulatory_registration_path" in stale_keys
        # The evidence item should now be marked stale in-place
        found = next(e for e in venture.evidence if e.id == old_e.id)
        assert found.stale is True

    def test_assumption_flagged_stale_when_evidence_expires(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())
        service.run_analysis_job(service.create_analysis_job(venture.id).id)
        venture = service.get_venture(venture.id)

        # Find an assumption that has a matching key in the standard set
        existing_keys = {a.key for a in venture.assumptions}
        target_key = next(iter(existing_keys), None)
        if target_key is None:
            pytest.skip("No assumptions to test")

        old_e = _old_evidence(target_key, EvidenceType.MODEL, 31)
        venture.evidence.append(old_e)

        mark_stale_evidence(venture)

        assumption = venture.assumption_map().get(target_key)
        if assumption:
            # assumption.stale should be True since model evidence is stale after 30 days
            assert assumption.stale is True


# ---------------------------------------------------------------------------
# MonitorSchedule + StateStore
# ---------------------------------------------------------------------------

class TestMonitorSchedulePersistence:
    def test_configure_and_retrieve_monitor(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())

        schedule = service.configure_monitor(
            venture.id, MonitorConfigRequest(enabled=True, interval_hours=24)
        )
        assert schedule.enabled is True
        assert schedule.interval_hours == 24
        assert schedule.venture_id == venture.id

        retrieved = service.get_monitor_schedule(venture.id)
        assert retrieved is not None
        assert retrieved.enabled is True
        assert retrieved.interval_hours == 24

    def test_update_monitor_schedule(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())

        service.configure_monitor(venture.id, MonitorConfigRequest(interval_hours=48))
        updated = service.configure_monitor(venture.id, MonitorConfigRequest(enabled=False, interval_hours=72))
        assert updated.enabled is False
        assert updated.interval_hours == 72

    def test_list_monitor_schedules(self, tmp_path):
        service = _make_service(tmp_path)
        v1 = service.create_venture(_supermarket_intake())
        v2 = service.create_venture(_supermarket_intake())

        service.configure_monitor(v1.id, MonitorConfigRequest())
        service.configure_monitor(v2.id, MonitorConfigRequest())

        schedules = service.state.list_monitor_schedules()
        ids = {s.venture_id for s in schedules}
        assert v1.id in ids
        assert v2.id in ids


# ---------------------------------------------------------------------------
# MonitorWorker.tick
# ---------------------------------------------------------------------------

class TestMonitorWorkerTick:
    def test_tick_skips_when_not_due(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())
        schedule = service.configure_monitor(venture.id, MonitorConfigRequest(interval_hours=168))
        # Set next_due_at far in the future
        schedule.next_due_at = datetime.now(UTC) + timedelta(days=7)
        service.state.save_monitor_schedule(schedule)

        worker = MonitorWorker()
        result = worker.tick(service, venture.id)
        assert result is not None
        # Should not have incremented check_count (not due)
        assert result.check_count == 0

    def test_tick_runs_when_due(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())

        # Configure monitor with next_due_at in the past
        schedule = service.configure_monitor(venture.id, MonitorConfigRequest(interval_hours=1))
        schedule.next_due_at = datetime(2000, 1, 1, tzinfo=UTC)
        service.state.save_monitor_schedule(schedule)

        worker = MonitorWorker()
        result = worker.tick(service, venture.id)
        assert result is not None
        assert result.check_count == 1
        assert result.last_checked_at is not None
        assert result.next_due_at > datetime.now(UTC)

    def test_tick_detects_stale_evidence_and_queues_recheck(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())

        # Plant old official evidence
        old_e = _old_evidence("regulatory_registration_path", EvidenceType.OFFICIAL, 91)
        venture.evidence.append(old_e)
        service.repository.save_venture(venture)

        # Configure monitor due immediately
        schedule = service.configure_monitor(venture.id, MonitorConfigRequest(interval_hours=1))
        schedule.next_due_at = datetime(2000, 1, 1, tzinfo=UTC)
        service.state.save_monitor_schedule(schedule)

        worker = MonitorWorker()
        result = worker.tick(service, venture.id)

        assert result is not None
        assert "regulatory_registration_path" in result.stale_assumption_keys

        # Check that MONITOR_STALE_DETECTED event was emitted
        events = service.state.list_events(venture.id)
        event_types = [e.event_type.value for e in events]
        assert "monitor_stale_detected" in event_types

    def test_tick_disabled_schedule_skips(self, tmp_path):
        service = _make_service(tmp_path)
        venture = service.create_venture(_supermarket_intake())

        schedule = service.configure_monitor(venture.id, MonitorConfigRequest(enabled=False))
        schedule.next_due_at = datetime(2000, 1, 1, tzinfo=UTC)
        service.state.save_monitor_schedule(schedule)

        worker = MonitorWorker()
        result = worker.tick(service, venture.id)
        # Disabled — should return without running
        assert result is not None
        assert result.check_count == 0
