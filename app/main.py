from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.domain import (
    AddEvidenceRequest,
    AnalysisJob,
    ApplyChangeRequest,
    Venture,
    VentureIntake,
)
from app.runtime import get_service
from app.service import VentureService
from app.settings import get_settings

settings = get_settings()
app = FastAPI(
    title="Venture Twin Agent",
    version="0.1.0",
    description="Persistent adversarial venture underwriting and execution agent.",
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
def ready() -> dict[str, str]:
    return {
        "status": "ready",
        "database_backend": settings.database_backend,
        "research_mode": settings.research_mode,
        "model": settings.gemini_model,
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


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
    """Deterministic synchronous hook for tests, demos and debugging."""
    try:
        job = service.create_analysis_job(venture_id)
        completed = service.run_analysis_job(job.id)
        if completed.status.value == "failed":
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
