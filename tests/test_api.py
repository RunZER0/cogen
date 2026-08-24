def test_health(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_exposes_runtime_mode(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert "model" in response.json()


def test_create_get_and_list_venture(client, intake_payload):
    created = client.post("/api/ventures", json=intake_payload)
    assert created.status_code == 201
    venture_id = created.json()["id"]
    fetched = client.get(f"/api/ventures/{venture_id}")
    assert fetched.status_code == 200
    assert fetched.json()["intake"]["location"].startswith("Ruiru")
    listed = client.get("/api/ventures")
    assert listed.status_code == 200
    assert any(item["id"] == venture_id for item in listed.json())


def test_missing_venture_returns_404(client):
    response = client.get("/api/ventures/nope")
    assert response.status_code == 404


def test_sync_analysis_runs_real_engine(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    response = client.post(f"/api/ventures/{venture_id}/analysis/sync")
    assert response.status_code == 200
    data = response.json()
    assert data["underwriting"]["simulation_runs"] == 5000
    assert data["evidence"]


def test_async_analysis_job_is_persisted(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    started = client.post(f"/api/ventures/{venture_id}/analysis")
    assert started.status_code == 202
    job_id = started.json()["id"]
    fetched = client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] in {"queued", "running", "complete"}


def test_demo_endpoint_is_honest_about_fixture(client):
    response = client.post("/api/demo")
    assert response.status_code == 201
    data = response.json()
    assert data["evidence"]
    assert all(e["evidence_type"] == "demo" for e in data["evidence"])


def test_add_evidence_changes_value(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    client.post(f"/api/ventures/{venture_id}/analysis/sync")
    response = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={
            "assumption_key": "transactions_per_day",
            "claim": "Observed validated count",
            "value": 160,
            "unit": "transactions/day",
            "evidence_type": "observed",
            "confidence": "verified",
            "source_title": "Founder observation",
        },
    )
    assert response.status_code == 200
    assumption = next(a for a in response.json()["assumptions"] if a["key"] == "transactions_per_day")
    assert assumption["value"] == 160
    assert assumption["confidence"] == "verified"


def test_apply_change_endpoint_re_underwrites(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    before = client.post(f"/api/ventures/{venture_id}/analysis/sync").json()
    response = client.post(
        f"/api/ventures/{venture_id}/changes",
        json={
            "summary": "New signed rent quote",
            "assumption_key": "monthly_rent",
            "new_value": 150000,
            "confidence": "verified",
        },
    )
    assert response.status_code == 200
    after = response.json()
    assert after["underwriting"]["monthly_operating_profit_base"] < before["underwriting"]["monthly_operating_profit_base"]


def test_root_serves_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Do the homework before the money goes out" in response.text
