"""Offline mock integrations (SRS Part 14). Selected when ONCALL_INTEGRATION_MODE=mock;
never used in production (config validation refuses it)."""
from .airflow import MockAirflowClient
from .github import MockGitHubClient
from .linear import AGENT_USER, MockLinearClient

__all__ = ["AGENT_USER", "MockAirflowClient", "MockGitHubClient", "MockLinearClient"]
