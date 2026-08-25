import pytest

from app.resilient_adk_model import ResilientGemini


class FakeRequest:
    def __init__(self, model="primary"):
        self.model = model
        self.marker = []

    def model_copy(self, deep=False):
        copy = FakeRequest(self.model)
        copy.marker = list(self.marker)
        return copy


class Fake503(Exception):
    code = 503


@pytest.mark.asyncio
async def test_transient_primary_failure_falls_back_with_fresh_request(monkeypatch):
    calls = []

    class FakeGemini:
        def __init__(self, model, retry_options=None):
            self.model = model
            self.retry_options = retry_options

        async def generate_content_async(self, request, stream=False):
            request.marker.append(self.model)
            calls.append((self.model, list(request.marker)))
            if self.model == "gemini-primary":
                raise Fake503("overloaded")
            yield {"model": self.model, "marker": list(request.marker)}

    monkeypatch.setattr("app.resilient_adk_model.Gemini", FakeGemini)
    model = ResilientGemini(
        model="gemini-primary",
        fallback_model="gemini-fallback",
        attempts_per_model=2,
    )
    original = FakeRequest()
    outputs = [item async for item in model.generate_content_async(original)]

    assert outputs == [{"model": "gemini-fallback", "marker": ["gemini-fallback"]}]
    assert calls == [
        ("gemini-primary", ["gemini-primary"]),
        ("gemini-fallback", ["gemini-fallback"]),
    ]
    assert original.marker == []
    assert model.snapshot()["fallback_count"] == 1
    assert model.snapshot()["last_successful_model"] == "gemini-fallback"


@pytest.mark.asyncio
async def test_non_transient_primary_error_does_not_fallback(monkeypatch):
    calls = []

    class Fake400(Exception):
        code = 400

    class FakeGemini:
        def __init__(self, model, retry_options=None):
            self.model = model

        async def generate_content_async(self, request, stream=False):
            calls.append(self.model)
            if False:
                yield None
            raise Fake400("bad request")

    monkeypatch.setattr("app.resilient_adk_model.Gemini", FakeGemini)
    model = ResilientGemini(model="gemini-primary", fallback_model="gemini-fallback")

    with pytest.raises(Fake400):
        _ = [item async for item in model.generate_content_async(FakeRequest())]
    assert calls == ["gemini-primary"]
    assert model.snapshot()["fallback_count"] == 0


@pytest.mark.asyncio
async def test_fallback_failure_is_not_hidden(monkeypatch):
    calls = []

    class FakeGemini:
        def __init__(self, model, retry_options=None):
            self.model = model

        async def generate_content_async(self, request, stream=False):
            calls.append(self.model)
            if False:
                yield None
            raise Fake503(self.model)

    monkeypatch.setattr("app.resilient_adk_model.Gemini", FakeGemini)
    model = ResilientGemini(model="gemini-primary", fallback_model="gemini-fallback")

    with pytest.raises(Fake503):
        _ = [item async for item in model.generate_content_async(FakeRequest())]
    assert calls == ["gemini-primary", "gemini-fallback"]
