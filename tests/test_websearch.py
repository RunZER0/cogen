import httpx
import pytest

from app.websearch import search_web


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_search_web_requires_tavily_key(monkeypatch):
    monkeypatch.setattr("app.websearch.get_settings", lambda: type(
        "S", (), {"tavily_api_key": None, "tavily_base_url": "https://api.tavily.com"}
    )())
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        search_web("Austin coffee shop competitors")


def test_search_web_maps_results(monkeypatch):
    monkeypatch.setattr("app.websearch.get_settings", lambda: type(
        "S", (), {"tavily_api_key": "test-key", "tavily_base_url": "https://api.tavily.com"}
    )())
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({
            "results": [
                {"title": "Competitor A", "url": "https://a.example.com", "content": "espresso bar"},
                {"title": "No URL", "content": "should be dropped"},
            ]
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    results = search_web("Austin coffee shop competitors", max_results=3)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["json"]["query"] == "Austin coffee shop competitors"
    assert captured["json"]["max_results"] == 3
    assert results == [{"title": "Competitor A", "url": "https://a.example.com", "content": "espresso bar"}]
