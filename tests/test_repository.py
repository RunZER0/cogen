from app.domain import AnalysisJob, FounderProfile, Venture, VentureIntake
from app.repository import SQLiteRepository


def test_sqlite_round_trip(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "repo.db"))
    venture = Venture(
        intake=VentureIntake(
            idea="Start a bakery",
            business_type="bakery",
            location="Nairobi, Kenya",
            founder=FounderProfile(available_capital=500_000),
        )
    )
    repo.save_venture(venture)
    loaded = repo.get_venture(venture.id)
    assert loaded is not None
    assert loaded.intake.idea == "Start a bakery"


def test_sqlite_job_round_trip(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "repo.db"))
    job = AnalysisJob(venture_id="venture-1")
    repo.save_job(job)
    loaded = repo.get_job(job.id)
    assert loaded is not None
    assert loaded.venture_id == "venture-1"
