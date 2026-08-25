from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ModelHealth:
    primary: str
    fallback: str | None
    last_model_used: str | None = None
    total_calls: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    fallbacks_used: int = 0

    def snapshot(self) -> dict[str, object]:
        return {
            "primary": self.primary,
            "fallback": self.fallback,
            "last_model_used": self.last_model_used,
            "total_calls": self.total_calls,
            "failures": dict(self.failures),
            "fallbacks_used": self.fallbacks_used,
        }


class GeminiModelRouter:
    """Small explicit model-sustenance layer for direct Gemini research calls.

    It retries a transiently failing model within a bounded budget, then moves to the configured stable
    fallback. It records health counters so readiness/observability can expose degradation instead of
    silently hiding it. The router never changes business state or treats a fallback response differently
    from primary evidence; the same evidence policy still applies afterwards.
    """

    def __init__(
        self,
        primary: str,
        *,
        fallback: str | None = None,
        attempts_per_model: int = 2,
    ):
        self.primary = primary
        self.fallback = fallback if fallback and fallback != primary else None
        self.attempts_per_model = max(1, attempts_per_model)
        self.health = ModelHealth(primary=primary, fallback=self.fallback)

    def generate(self, client: Any, *, contents: str, config: Any) -> Any:
        models = [self.primary] + ([self.fallback] if self.fallback else [])
        last_error: Exception | None = None
        self.health.total_calls += 1

        for model_index, model in enumerate(models):
            if model is None:
                continue
            for _ in range(self.attempts_per_model):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                    self.health.last_model_used = model
                    if model_index > 0:
                        self.health.fallbacks_used += 1
                    return response
                except Exception as exc:  # SDK error types vary across API/transports.
                    last_error = exc
                    self.health.failures[model] = self.health.failures.get(model, 0) + 1

        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini model router has no configured model")

    def snapshot(self) -> dict[str, object]:
        return self.health.snapshot()
