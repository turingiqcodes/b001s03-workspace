"""CLI behaviour available so far: `check` configuration validation and exit codes."""
from __future__ import annotations

import io
import subprocess
import sys

import pytest

from oncall_agent.cli import EXIT_CONFIG, EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main
from oncall_agent.factory import Integrations
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.providers.mock import MockAIProvider

from ..conftest import NO_ENV_FILE, PROJECT_ROOT

pytestmark = pytest.mark.unit


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["--env-file", str(NO_ENV_FILE), *argv], out=out)
    return code, out.getvalue()


def test_check_passes_offline_in_development(monkeypatch):  # AT-001
    monkeypatch.setenv("APP_ENV", "development")
    code, text = run(["check"])
    assert code == EXIT_OK
    assert "integrations      : mock" in text
    assert "configuration OK" in text
    for name in ("database", "airflow", "github", "linear", "ai", "labels"):
        assert f"OK    {name}" in text
    assert "all checks passed" in text


def test_check_fails_when_an_integration_is_unreachable(monkeypatch):  # Airflow unavailable
    monkeypatch.setenv("APP_ENV", "development")
    use_mock_clients(monkeypatch, airflow=True)
    code, text = run(["check"])
    assert code == EXIT_FAILURE
    assert "FAIL  airflow" in text and "OK    github" in text and "one or more checks FAILED" in text


def test_check_fails_when_the_ai_provider_is_unreachable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    use_mock_clients(monkeypatch, ai=True)
    code, text = run(["check"])
    assert code == EXIT_FAILURE and "FAIL  ai" in text and "OK    airflow" in text


def test_check_warns_about_missing_linear_labels(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    integrations = use_mock_clients(monkeypatch)
    integrations.linear.labels.pop("oncall:approved")
    code, text = run(["check"])
    assert code == EXIT_OK
    assert "missing in Linear (create them manually): oncall:approved" in text


def test_check_reports_missing_production_configuration(monkeypatch):  # AT-002
    monkeypatch.setenv("APP_ENV", "production")
    code, text = run(["check"])
    assert code == EXIT_CONFIG
    assert "AIRFLOW_BASE_URL" in text and "LINEAR_API_KEY" in text and "INVALID" in text


def test_check_reports_unloadable_configuration(monkeypatch):
    monkeypatch.setenv("ONCALL_MAX_AUTO_RETRIES", "lots")
    code, text = run(["check"])
    assert code == EXIT_CONFIG
    assert "policy.max_auto_retries" in text


def use_mock_clients(monkeypatch, **faults) -> Integrations:
    """Replace the real clients with mocks so CLI tests never touch the network."""
    integrations = Integrations(MockAirflowClient(), MockGitHubClient(), MockLinearClient(), mode="mock", ai=MockAIProvider())
    for name, value in faults.items():
        getattr(integrations, name).unavailable = value
    monkeypatch.setattr("oncall_agent.cli.build_integrations", lambda settings: integrations)
    return integrations


def test_check_never_prints_secrets(monkeypatch, real_env):
    for k, v in real_env.items():
        monkeypatch.setenv(k, v)
    use_mock_clients(monkeypatch)
    code, text = run(["check"])
    assert code == EXIT_OK, text
    for name in ("AIRFLOW_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "GROQ_API_KEY"):
        assert real_env[name] not in text


def test_missing_command_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_module_entry_point_runs():
    proc = subprocess.run([sys.executable, "-m", "oncall_agent", "--help"], capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert proc.returncode == 0 and "check" in proc.stdout
