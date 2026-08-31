import httpx
import pytest

from app.places import find_nearby_places


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_find_nearby_places_requires_maps_key(monkeypatch):
    monkeypatch.setattr("app.places.get_settings", lambda: type(
        "S", (), {"google_maps_api_key": None}
    )())
    with pytest.raises(RuntimeError, match="GOOGLE_MAPS_API_KEY"):
        find_nearby_places("pet grooming salons in Reno, Nevada")


def test_find_nearby_places_maps_results(monkeypatch):
    monkeypatch.setattr("app.places.get_settings", lambda: type(
        "S", (), {"google_maps_api_key": "test-key"}
    )())
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse({
            "places": [
                {
                    "displayName": {"text": "Pampered Paws Grooming"},
                    "formattedAddress": "123 Main St, Reno, NV",
                    "rating": 4.7,
                    "userRatingCount": 212,
                    "businessStatus": "OPERATIONAL",
                    "googleMapsUri": "https://maps.google.com/?cid=1",
                },
                {"displayName": {"text": ""}, "formattedAddress": "should be dropped"},
            ]
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    results = find_nearby_places("pet grooming salons in Reno, Nevada", max_results=5)

    assert captured["url"] == "https://places.googleapis.com/v1/places:searchText"
    assert captured["headers"]["X-Goog-Api-Key"] == "test-key"
    assert captured["json"]["textQuery"] == "pet grooming salons in Reno, Nevada"
    assert captured["json"]["maxResultCount"] == 5
    assert results == [{
        "name": "Pampered Paws Grooming",
        "address": "123 Main St, Reno, NV",
        "rating": 4.7,
        "rating_count": 212,
        "status": "OPERATIONAL",
        "maps_url": "https://maps.google.com/?cid=1",
    }]
