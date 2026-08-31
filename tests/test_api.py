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


def test_impossible_numeric_evidence_is_rejected(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    response = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={
            "assumption_key": "gross_margin_pct",
            "claim": "Impossible margin",
            "value": 2.5,
            "unit": "ratio",
            "evidence_type": "founder",
            "confidence": "verified",
            "source_title": "Founder",
        },
    )
    assert response.status_code == 422


def test_official_evidence_rejects_placeholder_and_accepts_state_authority(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    base = {
        "assumption_key": "regulatory_registration_path",
        "claim": "Texas LLC filing fee",
        "value": 300,
        "unit": "USD",
        "evidence_type": "official",
        "confidence": "verified",
        "source_title": "Texas Secretary of State",
    }
    rejected = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={**base, "source_url": "https://fake.gov/not-real"},
    )
    accepted = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={**base, "source_url": "https://www.sos.state.tx.us/corp/instructions/205.shtml"},
    )
    assert rejected.status_code == 422
    assert accepted.status_code == 200


def test_conflicting_founder_evidence_is_persisted(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    base = {
        "assumption_key": "monthly_rent",
        "unit": "KES/month",
        "evidence_type": "quote",
        "confidence": "verified",
    }
    first = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={**base, "claim": "Landlord A quote", "value": 50_000, "source_title": "Landlord A"},
    )
    second = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={**base, "claim": "Landlord B quote", "value": 100_000, "source_title": "Landlord B"},
    )
    contradictions = client.get(f"/api/ventures/{venture_id}/contradictions")
    assert first.status_code == second.status_code == 200
    assert contradictions.status_code == 200
    assert len(contradictions.json()) == 1
    assert contradictions.json()[0]["evidence_id_b"]


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


def test_demo_endpoint_returns_shell_while_analysis_runs_in_background(client):
    response = client.post("/api/demo")
    assert response.status_code == 201
    data = response.json()
    assert data["id"]
    assert data["evidence"] == []
    assert data["underwriting"] is None


def test_evidence_admission_rules_are_exposed_over_http(client, intake_payload):
    venture_id = client.post("/api/ventures", json=intake_payload).json()["id"]
    rejected = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={
            "assumption_key": "monthly_rent",
            "claim": "Official rent figure",
            "value": 45000,
            "unit": "KES/month",
            "evidence_type": "official",
            "confidence": "verified",
            "source_title": "Fake source",
            "source_url": "https://example.invalid/not-official",
        },
    )
    assert rejected.status_code == 422

    accepted = client.post(
        f"/api/ventures/{venture_id}/evidence",
        json={
            "assumption_key": "monthly_rent",
            "claim": "A model estimate",
            "value": 45000,
            "unit": "KES/month",
            "evidence_type": "model",
            "confidence": "verified",
            "source_title": "Scenario model",
        },
    )
    assert accepted.status_code == 200
    model_evidence = accepted.json()["evidence"][-1]
    assert model_evidence["confidence"] == "low"


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
    """The root serves the app shell; view content is rendered client-side by app.js."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="view"' in response.text
    assert "/static/app.js" in response.text
    assert "/static/styles.css" in response.text


def test_static_assets_are_served(client):
    for path in ("/static/app.js", "/static/styles.css"):
        assert client.get(path).status_code == 200
