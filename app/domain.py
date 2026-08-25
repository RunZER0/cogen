from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


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
    launch_target_months: int = Field(default=4, ge=1, le=60)
    founder: FounderProfile
    notes: str | None = Field(default=None, max_length=4000)


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
    supersedes_evidence_id: str | None = None

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
    rationale: list[str] = Field(default_factory=list)
    simulation_runs: int = 0
    calculated_at: datetime = Field(default_factory=utc_now)


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


class ApplyChangeRequest(BaseModel):
    summary: str
    assumption_key: str | None = None
    new_value: float | None = None
    source_title: str | None = None
    source_url: HttpUrl | None = None
    confidence: Confidence = Confidence.MEDIUM


class ForkVentureRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=500)
    location: str | None = Field(default=None, min_length=2, max_length=200)
    business_type: str | None = Field(default=None, min_length=2, max_length=100)
    assumption_overrides: dict[str, float] = Field(default_factory=dict)


class SandboxRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shocks: dict[str, float]
    simulation_runs: int = Field(default=5000, ge=100, le=100_000)


class IntakeDraftRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=500)
    known: dict[str, Any] = Field(default_factory=dict)


class ApiMessage(BaseModel):
    message: str
    data: dict[str, Any] | None = None
