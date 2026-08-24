from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    COMPLETE = "complete"
    FAILED = "failed"


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
    id: str = Field(default_factory=lambda: str(uuid4()))
    assumption_key: str | None = None
    claim: str
    value: float | str | None = None
    unit: str | None = None
    evidence_type: EvidenceType
    confidence: Confidence
    source_title: str
    source_url: HttpUrl | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None


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
    id: str = Field(default_factory=lambda: str(uuid4()))
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
    id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str
    assumption_key: str | None = None
    old_value: float | None = None
    new_value: float | None = None
    source_title: str | None = None
    source_url: HttpUrl | None = None
    confidence: Confidence = Confidence.MEDIUM
    occurred_at: datetime = Field(default_factory=utc_now)


class DecisionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    decision: Decision
    reason: str
    assumption_snapshot: dict[str, float | None]
    created_at: datetime = Field(default_factory=utc_now)


class Venture(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    intake: VentureIntake
    status: VentureStatus = VentureStatus.DISCOVER
    assumptions: list[Assumption] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    underwriting: UnderwritingResult | None = None
    roadmap: list[RoadmapStep] = Field(default_factory=list)
    changes: list[MaterialChange] = Field(default_factory=list)
    decision_history: list[DecisionRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def assumption_map(self) -> dict[str, Assumption]:
        return {item.key: item for item in self.assumptions}


class AnalysisJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    venture_id: str
    status: JobStatus = JobStatus.QUEUED
    message: str | None = None
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


class ApiMessage(BaseModel):
    message: str
    data: dict[str, Any] | None = None
