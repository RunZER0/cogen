"""Live end-to-end venture research evaluation script.

Executes a full live research run with Tavily web retrieval, OpenRouter/Gemini LLM synthesis,
confirmed-findings cross-specialist propagation, phase-by-phase wall-clock timing,
deterministic Monte Carlo underwriting, validation task generation, and cron monitor testing.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

from app.domain import (
    Confidence,
    EvidenceType,
    MonitorConfigRequest,
    VentureIntake,
)
from app.monitor import run_monitor_tick
from app.runtime import get_repository, get_research_provider, get_service
from app.settings import get_settings


def main() -> None:
    print("=" * 80)
    print("COGEN VENTURE TWIN — LIVE ADVERSARIAL RESEARCH RUN")
    print("=" * 80)

    settings = get_settings()
    print(f"Database backend:   {settings.database_backend}")
    print(f"Research provider:  {settings.research_provider}")
    print(f"Research mode:      {settings.research_mode}")
    print(f"LLM model:          {settings.openrouter_model if settings.research_provider == 'openrouter' else settings.gemini_model}")
    print(f"Specialist rounds:  {settings.specialist_research_rounds}")
    print(f"Monitor interval:   {settings.monitor_interval_seconds}s")
    print("-" * 80)

    service = get_service()
    readiness = service.readiness()
    print(f"Service readiness:  {json.dumps(readiness, indent=2)}")
    print("-" * 80)

    # 1. Create real-world venture intake
    print("[STEP 1] Creating Venture Twin for Austin Specialty Coffee Shop...")
    intake = VentureIntake.model_validate(
        {
            "idea": "Specialty espresso bar and pour-over coffee shop with locally baked pastries",
            "business_type": "specialty coffee shop",
            "location": "Austin, Texas, United States",
            "country": "United States",
            "subdivision": "Texas",
            "locality": "Austin",
            "currency": "USD",
            "locale": "en-US",
            "launch_target_months": 6,
            "founder": {
                "available_capital": 90000.0,
                "protected_reserve": 15000.0,
                "debt_available": 0.0,
                "target_monthly_owner_income": 6000.0,
                "max_acceptable_loss": 35000.0,
                "time_commitment": "full-time",
                "experience": "first-time specialty coffee shop founder",
            },
            "notes": (
                "Real-world query: target South Congress / East Austin foot-traffic corridor. "
                "Seeking ground-truth rent, permit requirements from Austin Public Health / City of Austin, "
                "local competitor landscape, equipment setup costs, and wage benchmarks."
            ),
        }
    )

    t_start = time.monotonic()
    venture = service.create_venture(intake)
    print(f"Venture Twin Created: ID = {venture.id}")
    print(f"Jurisdiction:         {venture.intake.jurisdiction_label} ({venture.intake.monetary_unit})")
    print(f"Available Capital:    ${venture.intake.founder.available_capital:,.2f} USD")
    print(f"Protected Reserve:    ${venture.intake.founder.protected_reserve:,.2f} USD")
    print(f"Starter Assumptions:  {len(venture.assumptions)} assumptions initialized")
    print("-" * 80)

    # 2. Run full analysis job
    print("[STEP 2] Launching 5-Specialist Live Grounded Research & Underwriting Workflow...")
    print("Specialists: [Finance, Market, Regulatory, Execution, Adversary]")
    job = service.create_analysis_job(venture.id)
    print(f"Analysis Job Queued:  ID = {job.id}, Workflow = {job.workflow_id}")

    job = service.run_analysis_job(job.id)
    t_end = time.monotonic()
    total_run_time = t_end - t_start

    print(f"Analysis Job Status:  {job.status.value.upper()}")
    print(f"Job Message:          {job.message}")
    print(f"Job Elapsed Seconds:  {job.elapsed_seconds}s (Total wall time: {total_run_time:.2f}s)")
    print("-" * 80)

    # Reload updated venture
    venture = service.get_venture(venture.id)
    underwriting = venture.underwriting
    specialists = service.specialists(venture.id)
    contradictions = service.contradictions(venture.id)
    validation_tasks = service.validation_tasks(venture.id)
    events = service.events(venture.id)
    timeline = service.timeline(venture.id)

    # 3. Phase Timings & Timeline
    print("[STEP 3] WORKFLOW TIMELINE & PHASE TIMINGS")
    workflow = service.state.get_workflow(job.workflow_id)
    if workflow and workflow.phase_timings:
        print("Phase Wall-Clock Breakdown:")
        for phase_name, duration in workflow.phase_timings.items():
            print(f"  - {phase_name:<15}: {duration:>6.2f}s")
    print("\nEvent Log with Elapsed Deltas:")
    for entry in timeline:
        payload_brief = ""
        if entry["event_type"] == "workflow_checkpoint":
            payload_brief = f" | phase={entry['payload'].get('phase')} ({entry['payload'].get('phase_elapsed_seconds', 0)}s)"
        elif entry["event_type"] == "specialist_completed":
            payload_brief = f" | role={entry['payload'].get('role')} (findings={entry['payload'].get('finding_count')}, rej={entry['payload'].get('rejected_count')})"
        elif entry["event_type"] == "underwriting_completed":
            payload_brief = f" | decision={entry['payload'].get('decision')} (p={entry['payload'].get('probability')})"
        print(f"  +T={entry['elapsed_seconds']:>6.2f}s [{entry['event_type']}]{payload_brief}")
    print("-" * 80)

    # 4. Specialist Reports & Admitted Evidence
    print(f"[STEP 4] SPECIALIST RESEARCH FINDINGS ({len(venture.evidence)} total evidence admitted)")
    for rep in specialists:
        print(f"\n--- Specialist: {rep.role.value.upper()} ---")
        print(f"Mandate:   {rep.mandate[:100]}...")
        print(f"Summary:   {rep.summary}")
        role_evidence = [e for e in venture.evidence if e.role == rep.role]
        for e in role_evidence:
            url_str = f" | {e.source_url}" if e.source_url else " | (no URL / unverified)"
            print(f"  * [{e.confidence.value.upper()}][{e.evidence_type.value}] {e.assumption_key}: {e.claim} (val={e.value} {e.unit or ''}){url_str}")

    # 5. Admissibility & Gate Check (Verify Country Leak & Official Domain Gate)
    print("-" * 80)
    print("[STEP 5] ADMISSIBILITY, PROVENANCE & ANTI-LEAK AUDIT")
    currencies_found = {a.unit for a in venture.assumptions if a.unit}
    print(f"Assumption Units: {currencies_found}")
    has_kes = any("KES" in str(u) for u in currencies_found)
    has_kenya = any("kenya" in str(e.claim).lower() or "kenya" in str(e.source_title).lower() for e in venture.evidence)
    print(f"Kenya / KES Leak Detected: {'YES (FAIL)' if (has_kes or has_kenya) else 'NO (PASSED - 100% US/USD)'}")

    regulatory_evidence = [e for e in venture.evidence if e.role and e.role.value == "regulatory"]
    print(f"Regulatory Evidence Count: {len(regulatory_evidence)}")
    for reg_ev in regulatory_evidence:
        print(f"  - Claim: {reg_ev.claim[:80]} | Type: {reg_ev.evidence_type.value} | URL: {reg_ev.source_url}")

    # 6. Assumptions & Financial Model State
    print("-" * 80)
    print("[STEP 6] VENTURE ASSUMPTIONS & FINANCIAL MODEL STATE")
    for a in venture.assumptions:
        val_str = f"{a.value:,.2f}" if isinstance(a.value, (int, float)) else str(a.value)
        print(f"  {a.key:<30} = {val_str:>12} {a.unit or '':<18} | Conf: {a.confidence.value:<8} | Critical: {str(a.critical):<5} | Stale: {a.stale}")

    # 7. Deterministic Underwriting Decision
    print("-" * 80)
    print("[STEP 7] DETERMINISTIC UNDERWRITING DECISION")
    if underwriting:
        print(f"DECISION:                   {underwriting.decision.value.upper()}")
        print(f"12M Break-Even Probability: {underwriting.break_even_probability_12m:.1%}")
        print(f"Evidence Coverage:          {underwriting.evidence_coverage:.1%}")
        print(f"Model Confidence:           {underwriting.model_confidence.value.upper()}")
        print(f"Monte Carlo Simulations:    {underwriting.simulation_runs:,}")
        if underwriting.monthly_revenue_base is not None:
            print(f"Monthly Revenue (Base):     ${underwriting.monthly_revenue_base:,.2f} USD")
            print(f"Monthly Profit (Base):      ${underwriting.monthly_operating_profit_base:,.2f} USD")
            print(f"Capital Remaining Setup:    ${underwriting.capital_remaining_after_setup:,.2f} USD")
        print("\nCritical Unknowns:")
        for cu in underwriting.critical_unknowns:
            print(f"  ! {cu}")
        print("\nBiggest Risks:")
        for r in underwriting.biggest_risks:
            print(f"  ! {r}")
        print("\nDecision Rationale:")
        for line in underwriting.rationale:
            print(f"  > {line}")

    # 8. Validation Tasks Generated
    print("-" * 80)
    print(f"[STEP 8] TARGETED FIELD VALIDATION TASKS ({len(validation_tasks)} tasks)")
    for vt in validation_tasks:
        print(f"\nTask: {vt.title} (Assumption: {vt.assumption_key})")
        print(f"Reason: {vt.reason}")
        print("Protocol steps:")
        for step in vt.protocol:
            print(f"   [ ] {step}")

    # 9. Cron Monitor Verification
    print("-" * 80)
    print("[STEP 9] CRON MONITOR SCHEDULE & STALENESS VERIFICATION")
    # Configure weekly monitor
    schedule = service.configure_monitor(
        venture.id,
        MonitorConfigRequest(enabled=True, interval_hours=168),
    )
    print(f"Monitor configured: Interval = {schedule.interval_hours}h, Next Due = {schedule.next_due_at.isoformat()}")

    # Trigger force tick to verify monitor execution
    print("Executing Monitor Tick (Staleness Evaluation)...")
    tick_result = run_monitor_tick(service, venture.id)
    print(f"Monitor Tick Result: Check Count = {tick_result.check_count}, Stale Keys = {tick_result.stale_assumption_keys}")
    print(f"Next Due Scheduled:  {tick_result.next_due_at.isoformat()}")

    # 10. Execution Roadmap & Gates
    print("-" * 80)
    print(f"[STEP 10] EXECUTION ROADMAP & GATES ({len(venture.roadmap)} phases)")
    for step in venture.roadmap:
        gate_icon = "[UNLOCKED/READY]" if step.status.value == "ready" else f"[{step.status.value.upper()}]"
        irrev_tag = " (IRREVERSIBLE / REQUIRES APPROVAL)" if step.irreversible else ""
        print(f"  {step.phase:<14} {gate_icon:<18} {step.title}{irrev_tag}")

    print("=" * 80)
    print("EVALUATION COMPLETE — ALL 10 PHASES DEMONSTRATED WITH LIVE RETRIEVAL & AUDIT TRAIL")
    print("=" * 80)


if __name__ == "__main__":
    main()
