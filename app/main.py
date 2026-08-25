from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    ContradictionRecord,
    ForkVentureRequest,
    IntakeDraft,
    IntakeDraftRequest,
    SandboxExperiment,
    SandboxRequest,
    SpecialistReport,
    ValidationTask,
    Venture,
    VentureEvent,
    VentureFork,
    VentureIntake,
)
from app.runtime import get_service
from app.service import VentureService
from app.settings import get_settings

settings = get_settings()
app = FastAPI(
    title="Cogen Venture Twin",
    version="0.2.0",
    description="Persistent adversarial venture underwriting, experimentation and execution agent.",
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def service_dep() -> VentureService:
    return get_service()


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
        "model": settings.gemini_model,
        "google_cloud_project": settings.google_cloud_project,
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


@app.post(
    "/api/ventures/{venture_id}/analysis",
    response_model=AnalysisJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_analysis(
    venture_id: str,
    background_tasks: BackgroundTasks,
    service: VentureService = Depends(service_dep),
) -> AnalysisJob:
    try:
        job = service.create_analysis_job(venture_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    background_tasks.add_task(service.run_analysis_job, job.id)
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


@app.get("/api/ventures/{venture_id}/events", response_model=list[VentureEvent])
def list_events(venture_id: str, service: VentureService = Depends(service_dep)) -> list[VentureEvent]:
    return service.events(venture_id)


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


@app.post("/api/demo", response_model=Venture, status_code=status.HTTP_201_CREATED)
def create_demo(service: VentureService = Depends(service_dep)) -> Venture:
    return service.demo_venture()
