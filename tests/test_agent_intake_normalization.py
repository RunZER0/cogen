from app.agent import _normalize_intake_payload


def test_agent_normalizes_flat_model_tool_arguments() -> None:
    payload = _normalize_intake_payload(
        {
            "idea": "Specialty coffee shop",
            "locality": "Austin",
            "state_subdivision": "Texas",
            "country": "United States",
            "currency": "USD",
            "total_capital": 85_000,
            "protected_reserve": 15_000,
            "debt_available": False,
            "founder_target_monthly_income": 7_500,
            "max_acceptable_loss": 30_000,
            "launch_window_months": 6,
            "operator_experience": "first-time cafe operator",
            "operator_time_commitment": "full-time",
        }
    )

    assert payload["location"] == "Austin, Texas, United States"
    assert payload["subdivision"] == "Texas"
    assert payload["launch_target_months"] == 6
    assert payload["founder"] == {
        "available_capital": 85_000,
        "protected_reserve": 15_000,
        "debt_available": 0.0,
        "target_monthly_owner_income": 7_500,
        "max_acceptable_loss": 30_000,
        "time_commitment": "full-time",
        "experience": "first-time cafe operator",
    }
