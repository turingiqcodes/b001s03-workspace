"""Common AI provider interface (FR-031).

A provider has one job: turn an :class:`AnalysisPrompt` into the model's raw text.
It never sees an Airflow client and cannot act on anything (FR-040). Parsing, schema
validation, fallback and every decision happen outside the provider
(``analysis.py`` and ``policy.py``), so adding a provider cannot weaken safety.

To add a provider: implement :class:`AIProvider`, add its name to
``config.AIProviderName`` and its settings/validation, and build it in
``factory.build_ai_provider``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisPrompt:
    """Provider-neutral prompt. ``system`` holds instructions and the JSON schema;
    ``user`` holds the incident data (serialised context)."""

    system: str
    user: str
    # The structured context the user message was built from, for providers that do
    # not call a model (the offline mock) and for debugging.
    data: dict[str, Any]


class AIProvider(ABC):
    """Base class for AI providers. Implementations must raise
    :class:`~oncall_agent.exceptions.AIProviderError` for any failure to produce text
    (timeout, HTTP error, refusal, truncated output)."""

    name: str = "provider"

    @property
    def model(self) -> str | None:
        return None

    @abstractmethod
    def complete(self, prompt: AnalysisPrompt) -> str:
        """Return the model's raw response text (expected to be one JSON object)."""

    @abstractmethod
    def check_connection(self) -> dict[str, Any]:
        """Verify credentials and model availability (used by ``oncall-agent check``)."""

    def close(self) -> None:  # pragma: no cover - default no-op
        pass
