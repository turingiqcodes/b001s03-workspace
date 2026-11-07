"""Wiring: builds the shared clients and services from Settings (NFR-017, NFR-038).

Each integration client is built once and shared by the agent, the CLI and the MCP
servers, so connection pools and Airflow tokens are reused. Mock vs real is chosen only
here, from ``settings.integration_mode``; business logic never checks the mode.
Production can never get a mock: config validation refuses it, and this factory checks
again (fail closed).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Settings
from .exceptions import ConfigError
from .integrations.airflow import AirflowClient, HttpAirflowClient
from .integrations.github import GitHubClient, HttpGitHubClient
from .integrations.linear import HttpLinearClient, LinearClient
from .providers.base import AIProvider
from .services.audit import AuditService
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Integrations:
    airflow: AirflowClient
    github: GitHubClient
    linear: LinearClient
    mode: str
    ai: AIProvider | None = None
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        for client in (self.airflow, self.github, self.linear, self.ai):
            if client is None:
                continue
            try:
                client.close()
            except Exception:  # closing must not mask the real error
                log.warning("Error closing %s", type(client).__name__, exc_info=True)
        self._closed = True


def build_integrations(settings: Settings) -> Integrations:
    if settings.use_mocks:
        if settings.is_production:
            raise ConfigError("Mock integrations are not allowed in production")
        from .integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient

        log.info("Using MOCK integrations (offline mode)")
        return Integrations(MockAirflowClient(), MockGitHubClient(), MockLinearClient(), mode="mock", ai=build_ai_provider(settings))

    return Integrations(
        airflow=HttpAirflowClient(settings.airflow),
        github=HttpGitHubClient(settings.github),
        linear=HttpLinearClient(settings.linear),
        mode="real",
        ai=build_ai_provider(settings),
    )


def build_ai_provider(settings: Settings) -> AIProvider:
    """FR-030/031/032: provider chosen by AI_PROVIDER (groq | mock)."""
    if settings.ai.provider == "mock":
        if settings.is_production:
            raise ConfigError("The mock AI provider is not allowed in production")
        from .providers.mock import MockAIProvider

        return MockAIProvider()
    if settings.ai.provider == "groq":
        from .providers.groq import GroqProvider

        return GroqProvider(settings.ai)
    raise ConfigError(f"Unsupported AI provider {settings.ai.provider!r}")  # pragma: no cover - config validation prevents it


def build_store(settings: Settings) -> Store:
    return Store(settings.storage.database_path).open()


def build_audit(settings: Settings, store: Store) -> AuditService:
    return AuditService(store, settings.logging.dir / "audit.jsonl")
