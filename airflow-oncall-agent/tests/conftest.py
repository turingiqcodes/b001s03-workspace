"""Shared test fixtures. Tests never read the developer's real .env or environment."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from oncall_agent.config import ENV_MAP, LOADER_ENV
from oncall_agent.logging_config import clear_registered_secrets
from oncall_agent.models import FailureType, Incident
from oncall_agent.services.audit import AuditService
from oncall_agent.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
NO_ENV_FILE = PROJECT_ROOT / "tests" / "__no_such_env_file__"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove every variable the loader reads, point .env at a missing file, and keep
    the database and logs of CLI-driven tests in a temporary directory."""
    for name in list(ENV_MAP) + list(LOADER_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ONCALL_ENV_FILE", str(NO_ENV_FILE))
    monkeypatch.setenv("ONCALL_DATABASE_PATH", str(tmp_path / "oncall.db"))
    monkeypatch.setenv("ONCALL_LOG_DIR", str(tmp_path / "logs"))
    clear_registered_secrets()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real service. httpx.MockTransport is unaffected."""

    def blocked(self, request):  # pragma: no cover - only hit by a buggy test
        raise RuntimeError(f"Real network access attempted in a test: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "state" / "oncall.db").open()
    yield s
    s.close()


@pytest.fixture
def audit(store: Store, tmp_path: Path) -> AuditService:
    return AuditService(store, tmp_path / "logs" / "audit.jsonl")


def make_incident(fingerprint_char: str = "a", **overrides) -> Incident:
    data = {
        "fingerprint": fingerprint_char * 64,
        "fingerprint_input": {"dag_id": "sales_etl", "task_id": "extract_orders", "normalized_error_line": "ReadTimeout"},
        "failure_type": FailureType.TASK_FAILURE,
        "dag_id": "sales_etl",
        "task_id": "extract_orders",
        "dag_run_id": "scheduled__2026-11-05T06:00:00+00:00",
        "original_error": "ReadTimeout: read timeout=30 at 0x7f00",
        "normalized_error": "ReadTimeout: read timeout=<n> at <addr>",
    }
    data.update(overrides)
    return Incident(**data)


@pytest.fixture
def real_env() -> dict[str, str]:
    """A complete, valid set of real-integration variables (fake values)."""
    return {
        "APP_ENV": "production",
        "AIRFLOW_BASE_URL": "https://airflow.example.com",
        "AIRFLOW_TOKEN": "airflow-jwt-token-value",
        "GITHUB_TOKEN": "ghp_" + "a" * 36,
        "GITHUB_REPOSITORY": "example-org/airflow-dags",
        "LINEAR_API_KEY": "lin_api_" + "b" * 32,
        "LINEAR_TEAM_ID": "team-123",
        "AI_PROVIDER": "groq",
        "GROQ_API_KEY": "gsk_" + "c" * 40,
        "GROQ_MODEL": "groq-test-model",
        "ONCALL_TRUSTED_APPROVERS": "alice@example.com,bob@example.com",
    }
