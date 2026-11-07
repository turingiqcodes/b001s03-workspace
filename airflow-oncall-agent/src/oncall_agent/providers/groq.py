"""Groq provider via its OpenAI-compatible Chat Completions API (FR-030 as amended:
Groq replaces Claude, deviation D-08; FR-033/034).

* ``POST {GROQ_BASE_URL}/chat/completions`` with ``temperature=0`` and
  ``response_format={"type": "json_object"}`` (JSON mode). The required schema is in
  the system prompt; the response is still fully validated by ``analysis.py`` because
  JSON mode guarantees syntax, not shape.
* Transient failures (timeout, 429, 5xx) are retried once; anything else raises
  :class:`AIProviderError`, which makes the analysis service use the fallback.
* Output cut off by the token limit (``finish_reason == "length"``) is rejected.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import AISettings
from ..exceptions import AIProviderError, IntegrationError
from ..integrations.http import json_body, send
from .base import AIProvider, AnalysisPrompt

log = logging.getLogger(__name__)


class _GroqHTTPError(IntegrationError):
    service = "groq"


class GroqProvider(AIProvider):
    name = "groq"

    def __init__(self, settings: AISettings, *, transport: httpx.BaseTransport | None = None) -> None:
        if settings.groq_api_key is None or not settings.groq_model:
            raise AIProviderError("GROQ_API_KEY and GROQ_MODEL must be configured")
        self._settings = settings
        kwargs: dict[str, Any] = {
            "base_url": settings.groq_base_url,
            "timeout": settings.timeout_seconds,
            "headers": {
                "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
                "Content-Type": "application/json",
                "User-Agent": "airflow-oncall-agent/1.0",
            },
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    @property
    def model(self) -> str | None:
        return self._settings.groq_model

    def close(self) -> None:
        self._client.close()

    def complete(self, prompt: AnalysisPrompt) -> str:
        body = {
            "model": self._settings.groq_model,
            "temperature": 0,
            "max_completion_tokens": self._settings.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        try:
            # Generating a completion has no side effects, so one retry is safe.
            response = send(self._client, "POST", "/chat/completions", error_cls=_GroqHTTPError, retries=1, idempotent=True, json=body)
            data = json_body(response, "groq")
        except IntegrationError as exc:
            raise AIProviderError(f"Groq request failed: {exc}") from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Groq response has no choices[0].message.content") from exc
        if finish == "length":
            raise AIProviderError("Groq output was cut off by the token limit (finish_reason=length)")
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError(f"Groq returned an empty response (finish_reason={finish})")
        usage = data.get("usage") or {}
        log.debug("Groq completion: model=%s finish=%s tokens=%s", self.model, finish, usage.get("total_tokens"))
        return content

    def check_connection(self) -> dict[str, Any]:
        """Verify the key works and the configured model is available."""
        try:
            response = send(self._client, "GET", "/models", error_cls=_GroqHTTPError, retries=1)
            data = json_body(response, "groq")
        except IntegrationError as exc:
            raise AIProviderError(f"Groq check failed: {exc}") from exc
        models = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)} if isinstance(data, dict) else set()
        if self.model not in models:
            raise AIProviderError(f"model {self.model!r} is not available to this Groq API key")
        return {"provider": "groq", "model": self.model}
