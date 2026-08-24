import pytest


def test_adk_agent_imports():
    try:
        import google.adk  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("google-adk is not installed in the execution sandbox; CI installs project deps")

    from app.agent import app, root_agent

    assert root_agent.name == "venture_underwriter"
    assert app.name == "app"
