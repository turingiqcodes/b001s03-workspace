"""Configuration loading and validation (AT-001, AT-002, FR-002/003/032/051/052, NFR-006..008)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oncall_agent.config import load_settings, validate_settings
from oncall_agent.exceptions import ConfigError, SecretStoreError

from ..conftest import CONFIG_DIR, NO_ENV_FILE, PROJECT_ROOT

pytestmark = pytest.mark.unit


def load(env: dict[str, str], **kwargs):
    kwargs.setdefault("config_dir", CONFIG_DIR)
    kwargs.setdefault("env_file", NO_ENV_FILE)
    return load_settings(env, **kwargs)


def error_keys(exc: ConfigError) -> set[str]:
    return {i.key for i in exc.issues if i.severity == "error"}


# --------------------------------------------------------------------------- AT-001 / AT-002


def test_development_runs_offline_with_mocks_and_no_credentials():
    s = load({"APP_ENV": "development"})
    assert s.app_env == "development"
    assert s.use_mocks is True
    assert s.ai.provider == "mock"
    assert not [i for i in validate_settings(s) if i.severity == "error"]


def test_valid_production_configuration_loads(real_env):  # AT-001
    s = load(real_env)
    assert s.is_production and s.integration_mode == "real"
    assert s.airflow.base_url == "https://airflow.example.com"
    assert s.airflow.auth_method == "token"
    assert s.ai.provider == "groq" and s.ai.groq_model == "groq-test-model"
    assert s.ai.groq_base_url == "https://api.groq.com/openai/v1"
    assert s.approval.trusted_approvers == ("alice@example.com", "bob@example.com")


def test_production_defaults_to_real_integrations(real_env):
    real_env.pop("APP_ENV")
    s = load({**real_env, "APP_ENV": "production"})
    assert s.integration_mode == "real"


def test_production_with_missing_credentials_refuses_to_start():  # AT-002
    with pytest.raises(ConfigError) as info:
        load({"APP_ENV": "production"})
    assert {
        "AIRFLOW_BASE_URL",
        "AIRFLOW_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "LINEAR_API_KEY",
        "LINEAR_TEAM_ID",
        "GROQ_API_KEY",
        "GROQ_MODEL",
    } <= error_keys(info.value)


@pytest.mark.parametrize("missing", ["AIRFLOW_BASE_URL", "GITHUB_TOKEN", "LINEAR_API_KEY", "LINEAR_TEAM_ID", "GROQ_API_KEY"])
def test_each_required_production_credential_is_enforced(real_env, missing):  # AT-002
    real_env.pop(missing)
    with pytest.raises(ConfigError) as info:
        load(real_env)
    assert missing in error_keys(info.value)


def test_production_never_falls_back_to_mock_integrations(real_env):  # decision A5
    with pytest.raises(ConfigError) as info:
        load({**real_env, "ONCALL_INTEGRATION_MODE": "mock"})
    assert "ONCALL_INTEGRATION_MODE" in error_keys(info.value)


def test_production_with_mock_mode_still_reports_credential_problems():
    with pytest.raises(ConfigError) as info:
        load({"APP_ENV": "production", "ONCALL_INTEGRATION_MODE": "mock"})
    assert {"ONCALL_INTEGRATION_MODE", "AIRFLOW_BASE_URL", "LINEAR_API_KEY"} <= error_keys(info.value)


def test_production_rejects_mock_ai_provider(real_env):
    with pytest.raises(ConfigError) as info:
        load({**real_env, "AI_PROVIDER": "mock"})
    assert "AI_PROVIDER" in error_keys(info.value)


def test_real_mode_in_development_also_requires_credentials():
    with pytest.raises(ConfigError) as info:
        load({"APP_ENV": "development", "ONCALL_INTEGRATION_MODE": "real", "AI_PROVIDER": "mock"})
    assert "AIRFLOW_BASE_URL" in error_keys(info.value)


def test_validate_false_returns_settings_for_reporting():
    s = load({"APP_ENV": "production"}, validate=False)
    assert any(i.severity == "error" for i in validate_settings(s))


def test_invalid_app_env_is_rejected():
    with pytest.raises(ConfigError) as info:
        load({"APP_ENV": "staging"})
    assert "APP_ENV" in error_keys(info.value)


def test_invalid_integration_mode_is_rejected():
    with pytest.raises(ConfigError):
        load({"ONCALL_INTEGRATION_MODE": "fake"})


# --------------------------------------------------------------------------- Airflow auth (FR-003)


def test_airflow_username_password_auth(real_env):
    real_env.pop("AIRFLOW_TOKEN")
    s = load({**real_env, "AIRFLOW_USERNAME": "oncall", "AIRFLOW_PASSWORD": "s3cret-pass"})
    assert s.airflow.auth_method == "password"


def test_airflow_token_and_password_together_is_an_error(real_env):
    with pytest.raises(ConfigError) as info:
        load({**real_env, "AIRFLOW_USERNAME": "oncall", "AIRFLOW_PASSWORD": "s3cret-pass"})
    assert "AIRFLOW_TOKEN" in error_keys(info.value)


@pytest.mark.parametrize("partial", [{"AIRFLOW_USERNAME": "oncall"}, {"AIRFLOW_PASSWORD": "s3cret-pass"}])
def test_airflow_incomplete_password_auth_is_an_error(real_env, partial):
    real_env.pop("AIRFLOW_TOKEN")
    with pytest.raises(ConfigError) as info:
        load({**real_env, **partial})
    assert "AIRFLOW_USERNAME" in error_keys(info.value)


@pytest.mark.parametrize("url", ["airflow.example.com", "ftp://airflow.example.com", "https://airflow.example.com/api/v2"])
def test_airflow_base_url_is_validated(real_env, url):  # FR-002
    with pytest.raises(ConfigError) as info:
        load({**real_env, "AIRFLOW_BASE_URL": url})
    assert "AIRFLOW_BASE_URL" in error_keys(info.value)


def test_airflow_base_url_trailing_slash_is_removed(real_env):
    assert load({**real_env, "AIRFLOW_BASE_URL": "https://af.example.com/"}).airflow.base_url == "https://af.example.com"


def test_github_repository_format(real_env):
    with pytest.raises(ConfigError) as info:
        load({**real_env, "GITHUB_REPOSITORY": "not-a-repo"})
    assert "GITHUB_REPOSITORY" in error_keys(info.value)


# --------------------------------------------------------------------------- AI provider (FR-032)


def test_groq_provider_requires_key_and_model_even_with_mock_integrations():
    with pytest.raises(ConfigError) as info:
        load({"APP_ENV": "development", "AI_PROVIDER": "groq"})
    assert {"GROQ_API_KEY", "GROQ_MODEL"} <= error_keys(info.value)


@pytest.mark.parametrize("provider", ["gpt", "claude"])
def test_unknown_ai_provider_is_rejected(provider):  # claude replaced by groq (deviation D-08)
    with pytest.raises(ConfigError):
        load({"AI_PROVIDER": provider})


def test_groq_base_url_is_configurable_and_validated(real_env):
    assert load({**real_env, "GROQ_BASE_URL": "https://proxy.example.com/v1/"}).ai.groq_base_url == "https://proxy.example.com/v1"
    with pytest.raises(ConfigError) as info:
        load({**real_env, "GROQ_BASE_URL": "proxy.example.com"})
    assert "GROQ_BASE_URL" in error_keys(info.value)


# --------------------------------------------------------------------------- retry limits (FR-051/052)


def test_max_auto_retries_zero_disables_automatic_retries():  # FR-051
    s = load({"ONCALL_MAX_AUTO_RETRIES": "0"})
    assert s.policy.max_auto_retries == 0
    assert s.auto_retries_enabled is False
    assert any(i.key == "ONCALL_MAX_AUTO_RETRIES" for i in validate_settings(s))


def test_global_limit_zero_disables_automatic_retries():
    assert load({"ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR": "0"}).auto_retries_enabled is False


def test_retry_limits_are_configurable():  # FR-052
    s = load({"ONCALL_MAX_AUTO_RETRIES": "5", "ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR": "3", "ONCALL_RETRY_VERIFY_TIMEOUT_MINUTES": "45"})
    assert (s.policy.max_auto_retries, s.policy.global_max_retries_per_hour, s.policy.retry_verify_timeout_minutes) == (5, 3, 45)


def test_default_retry_limits_match_srs():
    s = load({})
    assert s.policy.max_auto_retries == 2
    assert s.policy.global_max_retries_per_hour == 10
    assert s.policy.retry_verify_timeout_minutes == 30


@pytest.mark.parametrize("value", ["-1", "two", "1.5"])
def test_invalid_retry_limit_is_rejected(value):
    with pytest.raises(ConfigError) as info:
        load({"ONCALL_MAX_AUTO_RETRIES": value})
    assert "policy.max_auto_retries" in error_keys(info.value)


def test_poll_interval_is_configurable_and_validated():  # FR-004 / NFR-014
    assert load({"POLL_INTERVAL_SECONDS": "120"}).polling.interval_seconds == 120
    with pytest.raises(ConfigError):
        load({"POLL_INTERVAL_SECONDS": "0"})


def test_no_retry_dags_come_from_json():  # decision A4
    assert "finance_ledger_close" in load({"APP_ENV": "development"}).policy.no_retry_dags


# --------------------------------------------------------------------------- MCP and approvals


def test_mcp_writes_are_disabled_by_default():
    s = load({})
    assert s.mcp.allow_airflow_writes is False and s.mcp.allow_linear_writes is False


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("false", False), ("no", False)])
def test_mcp_write_flags_parse_booleans(raw, expected):
    assert load({"ONCALL_MCP_ALLOW_AIRFLOW_WRITES": raw}).mcp.allow_airflow_writes is expected


def test_invalid_boolean_is_rejected():
    with pytest.raises(ConfigError):
        load({"ONCALL_MCP_ALLOW_AIRFLOW_WRITES": "maybe"})


def test_empty_trusted_approvers_warns_in_real_mode(real_env):
    real_env.pop("ONCALL_TRUSTED_APPROVERS")
    issues = validate_settings(load(real_env))
    assert any(i.key == "ONCALL_TRUSTED_APPROVERS" and i.severity == "warning" for i in issues)


# --------------------------------------------------------------------------- precedence and sources


def test_env_overrides_environment_json_which_overrides_default():
    s = load({"APP_ENV": "development"})
    assert s.polling.interval_seconds == 15  # development.json over default.json (60)
    assert load({"APP_ENV": "development", "POLL_INTERVAL_SECONDS": "30"}).polling.interval_seconds == 30


def test_dotenv_file_is_read_but_real_environment_wins(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("POLL_INTERVAL_SECONDS=42\nONCALL_MAX_AUTO_RETRIES=1\n")
    s = load({"ONCALL_MAX_AUTO_RETRIES": "3"}, env_file=env_file)
    assert s.polling.interval_seconds == 42
    assert s.policy.max_auto_retries == 3


def test_empty_values_are_treated_as_not_set():
    s = load({"APP_ENV": "development", "AI_PROVIDER": "", "POLL_INTERVAL_SECONDS": "  ", "AIRFLOW_TOKEN": ""})
    assert s.ai.provider == "mock"
    assert s.polling.interval_seconds == 15
    assert s.airflow.token is None


def test_unknown_oncall_variable_produces_warning():
    s = load({"ONCALL_MAX_AUTO_RETRY": "5"})  # typo
    assert any("ONCALL_MAX_AUTO_RETRY" in i.message for i in validate_settings(s))


def _write_config(tmp_path: Path, default: dict, **envs: dict) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "default.json").write_text(json.dumps(default))
    for name, data in envs.items():
        (cfg / f"{name}.json").write_text(json.dumps(data))
    return cfg


def test_unknown_json_key_is_rejected(tmp_path: Path):
    cfg = _write_config(tmp_path, {"policy": {"max_auto_retrys": 3}})
    with pytest.raises(ConfigError) as info:
        load({}, config_dir=cfg)
    assert any("max_auto_retrys" in k for k in error_keys(info.value))


def test_malformed_json_file_is_reported(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "default.json").write_text("{not json")
    with pytest.raises(ConfigError, match="Cannot read config file"):
        load({}, config_dir=cfg)


def test_relative_paths_resolve_against_project_root():
    s = load({"ONCALL_DATABASE_PATH": "./data/x.db"})
    assert s.storage.database_path == (PROJECT_ROOT / "data" / "x.db").resolve()
    assert s.logging.dir == (PROJECT_ROOT / "logs").resolve()


# --------------------------------------------------------------------------- SSM (NFR-008)


def test_ssm_fills_missing_secrets_and_environment_still_wins(real_env):
    for name in ("AIRFLOW_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "GROQ_API_KEY"):
        real_env.pop(name)
    real_env["GITHUB_TOKEN"] = "ghp_" + "e" * 36  # provided by env: must not be fetched
    requested: list[str] = []

    def fake_fetch(names: list[str]) -> dict[str, str]:
        requested.extend(names)
        return {
            "/oncall/prod/AIRFLOW_TOKEN": "ssm-airflow-token",
            "/oncall/prod/LINEAR_API_KEY": "lin_api_" + "f" * 32,
            "/oncall/prod/GROQ_API_KEY": "gsk_" + "g" * 40,
            "/oncall/prod/GITHUB_TOKEN": "should-not-be-used",
        }

    s = load({**real_env, "ONCALL_SSM_PREFIX": "/oncall/prod/"}, ssm_fetcher=fake_fetch)
    assert "/oncall/prod/GITHUB_TOKEN" not in requested
    assert s.airflow.token.get_secret_value() == "ssm-airflow-token"
    assert s.github.token.get_secret_value() == "ghp_" + "e" * 36


def test_ssm_missing_parameter_fails_validation(real_env):
    real_env.pop("LINEAR_API_KEY")
    with pytest.raises(ConfigError) as info:
        load({**real_env, "ONCALL_SSM_PREFIX": "/p/"}, ssm_fetcher=lambda names: {})
    assert "LINEAR_API_KEY" in error_keys(info.value)


def test_ssm_failure_fails_closed(real_env):
    real_env.pop("LINEAR_API_KEY")

    def broken(names: list[str]) -> dict[str, str]:
        raise SecretStoreError("AccessDenied")

    with pytest.raises(ConfigError) as info:
        load({**real_env, "ONCALL_SSM_PREFIX": "/p/"}, ssm_fetcher=broken)
    assert "ONCALL_SSM_PREFIX" in error_keys(info.value)


# --------------------------------------------------------------------------- secrets (NFR-006/009)


def test_secrets_are_hidden_in_repr_and_errors(real_env):
    s = load(real_env)
    text = repr(s) + str(s.model_dump())
    for name in ("AIRFLOW_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "GROQ_API_KEY"):
        assert real_env[name] not in text
    assert set(s.secret_values()) == {real_env[n] for n in ("AIRFLOW_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "GROQ_API_KEY")}


def test_validation_error_does_not_echo_secret_values(real_env):
    with pytest.raises(ConfigError) as info:
        load({**real_env, "ONCALL_MAX_AUTO_RETRIES": "bad", "AIRFLOW_USERNAME": "u", "AIRFLOW_PASSWORD": "p@ss-word-123"})
    assert "p@ss-word-123" not in str(info.value)


def test_no_credentials_in_committed_config_files():
    for path in CONFIG_DIR.glob("*.json"):
        text = path.read_text().lower()
        for marker in ("gsk_", "sk-ant-", "lin_api_", "ghp_", "github_pat_", "password\": \""):
            assert marker not in text, f"{path.name} contains {marker}"


def test_missing_config_directory_fails_closed(tmp_path: Path):
    with pytest.raises(ConfigError) as info:
        load({}, config_dir=tmp_path / "nowhere")
    assert "ONCALL_CONFIG_DIR" in error_keys(info.value)
