from __future__ import annotations

import logging
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr


logger = logging.getLogger(__name__)


class ResilientGemini(BaseLlm):
    """ADK-native Gemini model wrapper with bounded cross-model failover.

    ADK/Google GenAI already retries transient errors inside a single Gemini model. This wrapper closes
    the availability gap above that layer: after the primary exhausts its bounded retries, only transient
    availability/rate/network failures are retried against the configured fallback Gemini model. Every model
    receives a deep copy of the same logical ADK LlmRequest so a failed attempt cannot mutate the request used
    by the fallback turn.
    """

    model: str = "gemini-3.7-flash"
    fallback_model: str | None = "gemini-3.6-flash"
    attempts_per_model: int = 2

    _fallback_count: int = PrivateAttr(default=0)
    _last_successful_model: str | None = PrivateAttr(default=None)

    @property
    def capabilities(self):
        return Gemini(model=self.model).capabilities

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        candidates = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            candidates.append(self.fallback_model)

        last_error: Exception | None = None
        for index, model_name in enumerate(candidates):
            delegate = Gemini(
                model=model_name,
                retry_options=types.HttpRetryOptions(
                    attempts=self.attempts_per_model,
                    initial_delay=1,
                    max_delay=20,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            )
            request = llm_request.model_copy(deep=True)
            request.model = model_name
            emitted = False
            try:
                async for response in delegate.generate_content_async(request, stream=stream):
                    emitted = True
                    self._last_successful_model = model_name
                    yield response
                return
            except Exception as exc:
                last_error = exc
                if emitted or not self._is_transient(exc) or index == len(candidates) - 1:
                    raise
                self._fallback_count += 1
                logger.warning(
                    "COGEN_ROOT_MODEL_FALLBACK primary=%s fallback=%s error=%s",
                    model_name,
                    candidates[index + 1],
                    self._error_code(exc) or type(exc).__name__,
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError("No Gemini model candidate configured")

    def snapshot(self) -> dict[str, object]:
        return {
            "primary_model": self.model,
            "fallback_model": self.fallback_model,
            "attempts_per_model": self.attempts_per_model,
            "fallback_count": self._fallback_count,
            "last_successful_model": self._last_successful_model,
        }

    @classmethod
    def _is_transient(cls, exc: Exception) -> bool:
        code = cls._error_code(exc)
        if code in {408, 429, 500, 502, 503, 504}:
            return True
        return isinstance(exc, (TimeoutError, ConnectionError))

    @staticmethod
    def _error_code(exc: Exception) -> int | None:
        for attr in ("code", "status_code"):
            value = getattr(exc, attr, None)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
