from __future__ import annotations

from datetime import UTC, datetime

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    Confidence,
    Evidence,
    JobStatus,
    MaterialChange,
    Venture,
    VentureIntake,
)
from app.engine import VentureEngine
from app.repository import VentureRepository
from app.research import ResearchProvider


class VentureService:
    def __init__(
        self,
        repository: VentureRepository,
        research_provider: ResearchProvider,
        engine: VentureEngine | None = None,
    ):
        self.repository = repository
        self.research_provider = research_provider
        self.engine = engine or VentureEngine()

    def create_venture(self, intake: VentureIntake) -> Venture:
        venture = self.engine.initialise(Venture(intake=intake))
        return self.repository.save_venture(venture)

    def list_ventures(self) -> list[Venture]:
        return self.repository.list_ventures()

    def get_venture(self, venture_id: str) -> Venture:
        venture = self.repository.get_venture(venture_id)
        if venture is None:
            raise KeyError("Venture not found")
        return venture

    def create_analysis_job(self, venture_id: str) -> AnalysisJob:
        self.get_venture(venture_id)
        job = AnalysisJob(venture_id=venture_id)
        return self.repository.save_job(job)

    def run_analysis_job(self, job_id: str) -> AnalysisJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("Analysis job not found")
        job.status = JobStatus.RUNNING
        job.message = "Researching and attacking the venture assumptions"
        job.updated_at = datetime.now(UTC)
        self.repository.save_job(job)
        try:
            venture = self.get_venture(job.venture_id)
            findings = self.research_provider.research(venture)
            venture = self.engine.ingest_research(venture, findings)
            venture = self.engine.underwrite(venture)
            self.repository.save_venture(venture)
            job.status = JobStatus.COMPLETE
            job.message = f"Analysis completed with decision: {venture.underwriting.decision.value}"
        except Exception as exc:  # noqa: BLE001 - persist failure for async job diagnosis
            job.status = JobStatus.FAILED
            job.message = f"{type(exc).__name__}: {exc}"
        job.updated_at = datetime.now(UTC)
        return self.repository.save_job(job)

    def get_job(self, job_id: str) -> AnalysisJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError("Analysis job not found")
        return job

    def add_evidence(self, venture_id: str, request: AddEvidenceRequest) -> Venture:
        venture = self.get_venture(venture_id)
        evidence = Evidence(
            assumption_key=request.assumption_key,
            claim=request.claim,
            value=request.value,
            unit=request.unit,
            evidence_type=request.evidence_type,
            confidence=request.confidence,
            source_title=request.source_title,
            source_url=request.source_url,
            notes=request.notes,
        )
        venture = self.engine.add_evidence(venture, evidence)
        return self.repository.save_venture(venture)

    def apply_change(self, venture_id: str, request: ApplyChangeRequest) -> Venture:
        venture = self.get_venture(venture_id)
        change = MaterialChange(
            summary=request.summary,
            assumption_key=request.assumption_key,
            new_value=request.new_value,
            source_title=request.source_title,
            source_url=request.source_url,
            confidence=request.confidence,
        )
        venture = self.engine.apply_change(venture, change)
        return self.repository.save_venture(venture)

    def complete_step(self, venture_id: str, step_id: str) -> Venture:
        venture = self.get_venture(venture_id)
        venture = self.engine.complete_roadmap_step(venture, step_id)
        return self.repository.save_venture(venture)

    def demo_venture(self) -> Venture:
        intake = VentureIntake.model_validate(
            {
                "idea": "Open a neighbourhood supermarket/minimart",
                "business_type": "supermarket retail",
                "location": "Ruiru, Kiambu County, Kenya",
                "launch_target_months": 4,
                "founder": {
                    "available_capital": 1_800_000,
                    "protected_reserve": 150_000,
                    "debt_available": 0,
                    "target_monthly_owner_income": 120_000,
                    "max_acceptable_loss": 600_000,
                    "time_commitment": "full-time",
                    "experience": "first-time retail founder",
                },
                "notes": "Canonical hackathon demo. Offline values are illustrative fixtures, not current Kenya market facts.",
            }
        )
        venture = self.create_venture(intake)
        job = self.create_analysis_job(venture.id)
        self.run_analysis_job(job.id)
        return self.get_venture(venture.id)
