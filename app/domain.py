from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


# Numeric assumptions are user/model inputs, so they need the same sanity boundary whether they
# arrive as founder evidence, a material change, a fork override, or a sandbox shock.  These are
# deliberately broad business-plausibility limits, not claims about what a particular venture
# should cost or sell for.
ASSUMPTION_VALUE_BOUNDS: dict[str, tuple[float, float]] = {
    "setup_costs": (0.0, 1_000_000_000_000.0),
    "monthly_rent": (0.0, 1_000_000_000_000.0),
    "monthly_payroll": (0.0, 1_000_000_000_000.0),
    "monthly_utilities": (0.0, 1_000_000_000_000.0),
    "gross_margin_pct": (0.0, 1.0),
    "average_basket": (0.0, 1_000_000_000_000.0),
    "transactions_per_day": (0.0, 1_000_000.0),
    "days_open_month": (1.0, 31.0),
    "shrinkage_pct": (0.0, 1.0),
    "regulatory_registration_path": (0.0, 1_000_000_000_000.0),
    "competition_local": (0.0, 1_000_000.0),
}


def validate_assumption_value(key: str, value: float | str | None) -> None:
    """Reject impossible numeric assumption values before they can affect underwriting."""
    if value is None or isinstance(value, str):
        return
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{key} must be a finite numeric value")
    bounds = ASSUMPTION_VALUE_BOUNDS.get(key)
    if bounds is None:
        return
    lower, upper = bounds
    if not lower <= float(value) <= upper:
        raise ValueError(
            f"{key} must be between {lower:g} and {upper:g}; received {value}"
        )


class VentureStatus(StrEnum):
    DISCOVER = "discover"
    UNDERWRITE = "underwrite"
    ATTACK = "attack"
    OPTIMISE = "optimise"
    EXECUTE = "execute"
    WATCH = "watch"
    KILLED = "killed"


class Decision(StrEnum):
    NEEDS_DATA = "needs_data"
    REJECT = "reject"
    CONDITIONAL = "conditional"
    APPROVE = "approve"


class Confidence(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERIFIED = "verified"


class AssumptionCategory(StrEnum):
    CAPITAL = "capital"
    COST = "cost"
    DEMAND = "demand"
    MARGIN = "margin"
    OPERATIONS = "operations"
    LOCATION = "location"
    REGULATORY = "regulatory"
    COMPETITION = "competition"
    EXECUTION = "execution"


class EvidenceType(StrEnum):
    OFFICIAL = "official"
    QUOTE = "quote"
    LISTING = "listing"
    REVIEW = "review"
    BENCHMARK = "benchmark"
    OBSERVED = "observed"
    FOUNDER = "founder"
    MODEL = "model"
    DEMO = "demo"


class GateStatus(StrEnum):
    LOCKED = "locked"
    READY = "ready"
    COMPLETE = "complete"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    RETRYABLE = "retryable"
    COMPLETE = "complete"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    RETRYABLE = "retryable"
    COMPLETE = "complete"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WorkflowPhase(StrEnum):
    PLAN = "plan"
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    UNDERWRITE = "underwrite"
    VALIDATION = "validation"
    MONITOR = "monitor"
    COMPLETE = "complete"


class SpecialistRole(StrEnum):
    FINANCE = "finance"
    MARKET = "market"
    REGULATORY = "regulatory"
    EXECUTION = "execution"
    ADVERSARY = "adversary"


class SubagentKind(StrEnum):
    SANDBOX = "sandbox"
    SPECIALIST = "specialist"


class SubagentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CRASHED = "crashed"


class EventType(StrEnum):
    VENTURE_CREATED = "venture_created"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_CHECKPOINT = "workflow_checkpoint"
    SPECIALIST_COMPLETED = "specialist_completed"
    EVIDENCE_ADDED = "evidence_added"
    EVIDENCE_REJECTED = "evidence_rejected"
    CONTRADICTION_DETECTED = "contradiction_detected"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    UNDERWRITING_COMPLETED = "underwriting_completed"
    VALIDATION_REQUIRED = "validation_required"
    MATERIAL_CHANGE = "material_change"
    FORK_CREATED = "fork_created"
    EXPERIMENT_COMPLETED = "experiment_completed"
    ROADMAP_COMPLETED = "roadmap_completed"
    MONITOR_STALE_DETECTED = "monitor_stale_detected"
    MONITOR_RECHECK_QUEUED = "monitor_recheck_queued"


class FounderProfile(BaseModel):
    available_capital: float = Field(gt=0)
    protected_reserve: float = Field(default=0, ge=0)
    debt_available: float = Field(default=0, ge=0)
    target_monthly_owner_income: float = Field(default=0, ge=0)
    max_acceptable_loss: float | None = Field(default=None, ge=0)
    time_commitment: str = "full-time"
    experience: str | None = None

    @field_validator("protected_reserve")
    @classmethod
    def reserve_cannot_be_absurd(cls, value: float) -> float:
        if value > 1_000_000_000_000:
            raise ValueError("protected_reserve is implausibly large")
        return value


class VentureIntake(BaseModel):
    idea: str = Field(min_length=3, max_length=500)
    business_type: str = Field(default="general", min_length=2, max_length=100)
    location: str = Field(min_length=2, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    subdivision: str | None = Field(default=None, max_length=120)
    locality: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    locale: str | None = Field(default=None, max_length=32)
    launch_target_months: int = Field(default=4, ge=1, le=60)
    founder: FounderProfile
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("country", "subdivision", "locality")
    @classmethod
    def normalize_geography(cls, value: str | None) -> str | None:
        return value.strip() if value else None

    @property
    def monetary_unit(self) -> str:
        return self.currency or "LOCAL"

    @property
    def jurisdiction_label(self) -> str:
        parts = [self.locality, self.subdivision, self.country]
        resolved = [part for part in parts if part]
        return ", ".join(resolved) if resolved else self.location


# Staleness windows by category (days). Evidence older than this is auto-flagged stale.
EVIDENCE_STALE_DAYS_DEFAULT: dict[str, int] = {
    "official": 90,
    "quote": 180,
    "listing": 90,
    "review": 180,
    "benchmark": 180,
    "observed": 365,
    "founder": 730,
    "model": 30,
    "demo": 730,
}


class Evidence(BaseModel):
    id: str = Field(default_factory=new_id)
    assumption_key: str | None = None
    claim: str
    value: float | str | None = None
    unit: str | None = None
    evidence_type: EvidenceType
    confidence: Confidence
    source_title: str
    source_url: HttpUrl | None = None
    source_published_at: datetime | None = None
    accessed_at: datetime = Field(default_factory=utc_now)
    observed_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None
    role: SpecialistRole | None = None
    materiality: float = Field(default=1.0, ge=0, le=10)
    fingerprint: str | None = None
    stale: bool = False
    stale_after_days: int | None = Field(
        default=None,
        ge=1,
        description="Days before this evidence is considered stale. None = use type default.",
    )
    supersedes_evidence_id: str | None = None

    def effective_stale_after_days(self) -> int:
        """Return the staleness window in days, falling back to the type-based default."""
        if self.stale_after_days is not None:
            return self.stale_after_days
        return EVIDENCE_STALE_DAYS_DEFAULT.get(self.evidence_type.value, 365)

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return True if the evidence is older than its staleness window."""
        ref = now or utc_now()
        age = ref - self.observed_at.replace(tzinfo=UTC) if self.observed_at.tzinfo is None else ref - self.observed_at
        return age.days >= self.effective_stale_after_days()

    @model_validator(mode="after")
    def set_fingerprint(self) -> "Evidence":
        if self.fingerprint:
            return self
        payload = {
            "assumption_key": self.assumption_key,
            "claim": self.claim.strip().lower(),
            "value": self.value,
            "unit": self.unit,
            "evidence_type": self.evidence_type.value,
            "source_title": self.source_title.strip().lower(),
            "source_url": str(self.source_url) if self.source_url else None,
        }
        raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        self.fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self


class Assumption(BaseModel):
    key: str
    label: str
    category: AssumptionCategory
    value: float | None = None
    unit: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    critical: bool = False
    impact_weight: float = Field(default=1.0, gt=0, le=10)
    evidence_ids: list[str] = Field(default_factory=list)
    source_note: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    stale: bool = False
    updated_at: datetime = Field(default_factory=utc_now)


class RescueCandidate(BaseModel):
    """The single assumption shift that most improves monthly operating profit, when the base
    case is already cash-negative — computed once, server-side, at underwrite time (see
    app/simulation.py's identify_rescue_candidate) so it's an authoritative fact on the venture
    itself rather than a number only the client-side Model tab knows how to recompute."""

    assumption_key: str
    assumption_label: str
    shocked_value: float
    monthly_operating_profit_at_shock: float
    gain: float


class UnderwritingResult(BaseModel):
    decision: Decision
    break_even_probability_12m: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    model_confidence: Confidence
    monthly_revenue_base: float | None = None
    monthly_operating_profit_base: float | None = None
    capital_remaining_after_setup: float | None = None
    critical_unknowns: list[str] = Field(default_factory=list)
    biggest_risks: list[str] = Field(default_factory=list)
    rescue_candidate: RescueCandidate | None = None
    rationale: list[str] = Field(default_factory=list)
    simulation_runs: int = 0
    calculated_at: datetime = Field(default_factory=utc_now)
    narrative: str | None = Field(
        default=None,
        description=(
            "LLM-authored, founder-facing prose leading the Position tab (see app/narrative.py). "
            "Always None on a freshly computed UnderwritingResult — generated lazily, on first "
            "read, by service.get_or_generate_narrative and cached back onto this same result, so "
            "any re-underwrite (a new UnderwritingResult replacing this one) naturally starts "
            "blank again rather than needing separate invalidation bookkeeping."
        ),
    )


class RoadmapStep(BaseModel):
    id: str = Field(default_factory=new_id)
    phase: str
    title: str
    description: str
    status: GateStatus = GateStatus.LOCKED
    irreversible: bool = False
    requires_user_approval: bool = False
    research_query: str | None = None
    official_source_required: bool = False
    dependency_ids: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None


class MaterialChange(BaseModel):
    id: str = Field(default_factory=new_id)
    summary: str
    assumption_key: str | None = None
    old_value: float | None = None
    new_value: float | None = None
    source_title: str | None = None
    source_url: HttpUrl | None = None
    confidence: Confidence = Confidence.MEDIUM
    occurred_at: datetime = Field(default_factory=utc_now)


class DecisionRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    decision: Decision
    reason: str
    assumption_snapshot: dict[str, float | None]
    created_at: datetime = Field(default_factory=utc_now)


class Venture(BaseModel):
    id: str = Field(default_factory=new_id)
    intake: VentureIntake
    status: VentureStatus = VentureStatus.DISCOVER
    assumptions: list[Assumption] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    underwriting: UnderwritingResult | None = None
    roadmap: list[RoadmapStep] = Field(default_factory=list)
    changes: list[MaterialChange] = Field(default_factory=list)
    decision_history: list[DecisionRecord] = Field(default_factory=list)
    parent_venture_id: str | None = None
    fork_label: str | None = None
    fork_reason: str | None = None
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def assumption_map(self) -> dict[str, Assumption]:
        return {item.key: item for item in self.assumptions}


class AnalysisJob(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    status: JobStatus = JobStatus.QUEUED
    message: str | None = None
    idempotency_key: str | None = None
    workflow_id: str | None = None
    attempt: int = 0
    elapsed_seconds: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class VentureEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    event_type: EventType
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "system"
    occurred_at: datetime = Field(default_factory=utc_now)


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    idempotency_key: str
    status: WorkflowStatus = WorkflowStatus.QUEUED
    phase: WorkflowPhase = WorkflowPhase.PLAN
    completed_phases: list[WorkflowPhase] = Field(default_factory=list)
    attempt: int = 0
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    phase_timings: dict[str, float] = Field(
        default_factory=dict,
        description="Wall-clock seconds spent in each phase, keyed by phase name.",
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchBatch(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    workflow_id: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SpecialistReport(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    workflow_id: str
    role: SpecialistRole
    mandate: str
    finding_count: int = 0
    rejected_count: int = 0
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ResearchProgress(BaseModel):
    """Live read of the most recent specialist-research workflow for a venture.

    Sourced entirely from WorkflowRunner's own checkpoint records (WorkflowRun, SpecialistReport) —
    not a separate tracking mechanism, so it can never drift from what the workflow actually did.
    """

    status: str = "none"
    phase: str | None = None
    phases_done: list[str] = Field(default_factory=list)
    specialists_total: list[str] = Field(default_factory=lambda: [r.value for r in SpecialistRole])
    specialists_done: list[str] = Field(default_factory=list)
    elapsed_seconds: float | None = None


class ContradictionRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    assumption_key: str
    evidence_id_a: str | None = None
    evidence_id_b: str | None = None
    description: str
    materiality: float = Field(default=1.0, ge=0, le=10)
    resolved: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ValidationTask(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    assumption_key: str
    title: str
    protocol: list[str]
    reason: str
    status: str = "open"
    created_at: datetime = Field(default_factory=utc_now)


class VentureFork(BaseModel):
    id: str = Field(default_factory=new_id)
    parent_venture_id: str
    child_venture_id: str
    label: str
    reason: str
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    invalidated_assumptions: list[str] = Field(default_factory=list)
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class SandboxExperiment(BaseModel):
    id: str = Field(default_factory=new_id)
    venture_id: str
    name: str
    shocks: dict[str, float]
    simulation_runs: int = 5000
    baseline_probability: float | None = None
    scenario_probability: float | None = None
    baseline_decision: Decision | None = None
    scenario_decision: Decision | None = None
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SubagentRun(BaseModel):
    """A standalone, durable subagent execution — a sandbox scenario or one specialist's research.

    Persisted incrementally (not just at completion) via SubagentEvent rows, so a crash mid-run
    still leaves everything it did up to that point inspectable, and a stale RUNNING row past its
    heartbeat can be detected and recovered after a process restart.
    """

    id: str = Field(default_factory=new_id)
    kind: SubagentKind
    venture_id: str
    workflow_id: str | None = None
    role: SpecialistRole | None = None
    batch_id: str | None = None
    parent_session_id: str | None = Field(
        default=None,
        description="Chat session to wake on completion (e.g. 'venture:{id}'); None if not chat-triggered.",
    )
    status: SubagentStatus = SubagentStatus.QUEUED
    input_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] | None = None
    error: str | None = None
    attempt: int = 0
    round_index: int = 0
    event_seq: int = 0
    heartbeat_at: datetime = Field(default_factory=utc_now)
    woken: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class SubagentEvent(BaseModel):
    """One narrated step of a SubagentRun — the same tool_call/tool_result/text/final/error shapes
    the main chat agent already streams over SSE, so both share one frontend rendering path."""

    id: str = Field(default_factory=new_id)
    run_id: str
    venture_id: str
    seq: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class IntakeDraft(BaseModel):
    id: str = Field(default_factory=new_id)
    idea: str = Field(min_length=3, max_length=500)
    known: dict[str, Any] = Field(default_factory=dict)
    next_question: str | None = None
    missing_material_fields: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AddEvidenceRequest(BaseModel):
    assumption_key: str
    claim: str
    value: float | str | None = None
    unit: str | None = None
    evidence_type: EvidenceType = EvidenceType.OBSERVED
    confidence: Confidence = Confidence.MEDIUM
    source_title: str
    source_url: HttpUrl | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "AddEvidenceRequest":
        validate_assumption_value(self.assumption_key, self.value)
        return self


class ApplyChangeRequest(BaseModel):
    summary: str
    assumption_key: str | None = None
    new_value: float | None = None
    source_title: str | None = None
    source_url: HttpUrl | None = None
    confidence: Confidence = Confidence.MEDIUM

    @model_validator(mode="after")
    def validate_value(self) -> "ApplyChangeRequest":
        if self.assumption_key:
            validate_assumption_value(self.assumption_key, self.new_value)
        return self


class ForkVentureRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=500)
    location: str | None = Field(default=None, min_length=2, max_length=200)
    business_type: str | None = Field(default=None, min_length=2, max_length=100)
    assumption_overrides: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_overrides(self) -> "ForkVentureRequest":
        for key, value in self.assumption_overrides.items():
            validate_assumption_value(key, value)
        return self


class SandboxRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shocks: dict[str, float]
    simulation_runs: int = Field(default=5000, ge=100, le=100_000)

    @model_validator(mode="after")
    def validate_shocks(self) -> "SandboxRequest":
        if not self.shocks:
            raise ValueError("shocks must include at least one assumption")
        for key, value in self.shocks.items():
            validate_assumption_value(key, value)
        return self


class IntakeDraftRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=500)
    known: dict[str, Any] = Field(default_factory=dict)


class MonitorSchedule(BaseModel):
    """Persistent cron schedule for time-sensitive evidence re-checking."""

    id: str = Field(default_factory=new_id)
    venture_id: str
    enabled: bool = True
    interval_hours: int = Field(default=168, ge=1, description="Hours between re-checks (default: weekly).")
    last_checked_at: datetime | None = None
    next_due_at: datetime = Field(default_factory=utc_now)
    stale_assumption_keys: list[str] = Field(default_factory=list)
    check_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


ALLOWED_ATTACHMENT_MIME_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ChatAttachment(BaseModel):
    """One file attached to a chat turn, base64-encoded inline (no separate upload step — the
    founder drops a photo of a lease, a competitor's storefront, an invoice, a PDF contract or a
    .docx brief straight into the same conversation the agent already runs everything through).

    Images and PDFs are sent to the multimodal model directly as bytes. Word documents are
    text-extracted and delivered as text (Gemini's document handling in this path reads the
    extracted text most reliably), so any of these lands as usable context.
    """

    mime_type: str
    data: str = Field(max_length=16_000_000, description="Base64-encoded file bytes.")
    name: str = Field(default="", max_length=300, description="Original filename for identification.")

    @field_validator("mime_type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in ALLOWED_ATTACHMENT_MIME_TYPES:
            raise ValueError(f"unsupported attachment type: {value}")
        return value


class ChatMessageRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def require_text_or_attachment(self) -> "ChatMessageRequest":
        if not self.message.strip() and not self.attachments:
            raise ValueError("message must not be empty unless an attachment is included")
        return self


class MonitorConfigRequest(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(default=168, ge=1, le=8760)


class ApiMessage(BaseModel):
    message: str
    data: dict[str, Any] | None = None
