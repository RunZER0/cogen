import pytest
from fastapi.testclient import TestClient

from app.main import app, service_dep
from app.repository import SQLiteRepository
from app.research import OfflineResearchProvider
from app.service import VentureService


@pytest.fixture
def service(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "test.db"))
    return VentureService(repo, OfflineResearchProvider())


@pytest.fixture
def client(service):
    app.dependency_overrides[service_dep] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def intake_payload():
    return {
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
    }
