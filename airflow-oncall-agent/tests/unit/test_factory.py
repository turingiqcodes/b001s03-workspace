"""Client wiring (NFR-017/038, decision A5)."""
from __future__ import annotations

import pytest

from oncall_agent.config import load_settings
from oncall_agent.exceptions import ConfigError
from oncall_agent.factory import build_audit, build_integrations, build_store
from oncall_agent.integrations.airflow import HttpAirflowClient
from oncall_agent.integrations.github import HttpGitHubClient
from oncall_agent.integrations.linear import HttpLinearClient
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.unit


def settings(env: dict[str, str], validate: bool = True):
    return load_settings(env, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE, validate=validate)


def test_mock_mode_builds_mock_clients():
    integrations = build_integrations(settings({"APP_ENV": "development"}))
    assert integrations.mode == "mock"
    assert isinstance(integrations.airflow, MockAirflowClient)
    assert isinstance(integrations.github, MockGitHubClient)
    assert isinstance(integrations.linear, MockLinearClient)
    integrations.close()
    integrations.close()  # idempotent


def test_real_mode_builds_http_clients_without_network(real_env):
    integrations = build_integrations(settings(real_env))
    assert integrations.mode == "real"
    assert isinstance(integrations.airflow, HttpAirflowClient)
    assert isinstance(integrations.github, HttpGitHubClient)
    assert isinstance(integrations.linear, HttpLinearClient)
    integrations.close()


def test_factory_refuses_mocks_in_production_even_if_validation_was_skipped(real_env):
    s = settings({**real_env, "ONCALL_INTEGRATION_MODE": "mock"}, validate=False)
    with pytest.raises(ConfigError):
        build_integrations(s)


def test_store_and_audit_use_configured_paths(tmp_path):
    s = settings({"ONCALL_DATABASE_PATH": str(tmp_path / "x.db"), "ONCALL_LOG_DIR": str(tmp_path / "logs")})
    store = build_store(s)
    audit = build_audit(s, store)
    assert (tmp_path / "x.db").exists()
    assert audit.jsonl_path == tmp_path / "logs" / "audit.jsonl"
    store.close()
