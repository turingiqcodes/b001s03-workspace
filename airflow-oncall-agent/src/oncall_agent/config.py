"""Configuration loading and validation (FR-002/003/032/051/052, NFR-006/007/008/014/038).

Sources, highest precedence first:

1. Process environment variables.
2. A ``.env`` file (local development only; never overrides real environment variables).
3. AWS SSM Parameter Store, for secret values only, when ``ONCALL_SSM_PREFIX`` is set.
4. ``config/<APP_ENV>.json``.
5. ``config/default.json``.

Empty values are treated as "not set" everywhere, so a copied ``.env.example`` with
blank placeholders falls back to the JSON defaults instead of overriding them with "".

Loading and validation are separate steps: :func:`load_settings` builds a typed
:class:`Settings`, and :func:`validate_settings` returns every problem found so the
``check`` command can show all of them at once. ``load_settings(validate=True)``
raises :class:`ConfigError` if any problem is an error, which is how the agent
fails safely when production credentials are missing (decision A5).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .exceptions import ConfigError, ConfigIssue, SecretStoreError

log = logging.getLogger(__name__)

# src/oncall_agent/config.py -> project root is two levels above the package.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

AppEnv = Literal["development", "test", "production"]
IntegrationMode = Literal["mock", "real"]
AIProviderName = Literal["groq", "mock"]


# ============================================================================ typed settings


class _Section(BaseModel):
    # extra="forbid" turns a typo in a JSON key into a startup error instead of a
    # silently ignored setting.
    model_config = ConfigDict(extra="forbid", frozen=True)


class AirflowSettings(_Section):
    base_url: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    token: SecretStr | None = None
    timeout_seconds: float = Field(30, gt=0, le=300)
    verify_tls: bool = True
    page_size: int = Field(100, ge=1, le=1000)
    max_retries_on_transient_error: int = Field(2, ge=0, le=10)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @property
    def auth_method(self) -> Literal["token", "password", "none", "conflict", "incomplete"]:
        """Which supported authentication method is configured (see .env.example)."""
        has_token = self.token is not None
        has_user, has_pass = self.username is not None, self.password is not None
        if has_token and (has_user or has_pass):
            return "conflict"
        if has_token:
            return "token"
        if has_user and has_pass:
            return "password"
        if has_user or has_pass:
            return "incomplete"
        return "none"


class GitHubSettings(_Section):
    token: SecretStr | None = None
    repository: str | None = None
    dag_path_prefix: str = "dags/"
    lookback_days: int = Field(7, ge=1, le=90)
    max_commits: int = Field(10, ge=1, le=100)
    timeout_seconds: float = Field(30, gt=0, le=300)


class LinearLabels(_Section):
    base: str = "oncall"
    approved: str = "oncall:approved"
    classification: dict[str, str] = Field(default_factory=dict)
    import_error: str = "oncall:import-error"
    awaiting_approval: str = "oncall:awaiting-approval"
    escalated: str = "oncall:escalated"


class LinearSettings(_Section):
    api_key: SecretStr | None = None
    team_id: str | None = None
    project_id: str | None = None
    default_assignee: str | None = None
    timeout_seconds: float = Field(30, gt=0, le=300)
    labels: LinearLabels = Field(default_factory=LinearLabels)
    priority: dict[str, int] = Field(default_factory=dict)
    states: dict[str, str] = Field(default_factory=dict)

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, v: dict[str, int]) -> dict[str, int]:
        for key, value in v.items():
            if not 0 <= value <= 4:
                raise ValueError(f"priority '{key}' must be 0-4 (Linear scale), got {value}")
        return v


class AISettings(_Section):
    provider: AIProviderName = "groq"
    groq_api_key: SecretStr | None = None
    groq_model: str | None = None  # required; no built-in default on purpose
    # Groq's OpenAI-compatible endpoint. Overridable for proxies / testing.
    groq_base_url: str = "https://api.groq.com/openai/v1"

    @field_validator("groq_base_url")
    @classmethod
    def _strip_base(cls, v: str) -> str:
        return v.rstrip("/")
    timeout_seconds: float = Field(60, gt=0, le=600)
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_log_chars: int = Field(20000, ge=1000)
    max_source_chars: int = Field(20000, ge=1000)
    max_patch_chars: int = Field(15000, ge=1000)


class PollingSettings(_Section):
    interval_seconds: int = Field(60, ge=1, le=86400)
    # Each poll re-reads this many seconds before the watermark so events that land
    # slightly out of order are not missed; duplicates are removed by event key.
    overlap_seconds: int = Field(300, ge=0, le=86400)
    # On the very first run (no watermark yet), look back this far.
    initial_lookback_minutes: int = Field(60, ge=1, le=10080)


class AnalysisSettings(_Section):
    worker_threads: int = Field(2, ge=1, le=16)


class PolicySettings(_Section):
    # Business rules from FR-050/051/062. The 0.7 confidence threshold is fixed by the
    # SRS and deliberately NOT configurable (see policy.py).
    max_auto_retries: int = Field(2, ge=0, le=100)
    global_max_retries_per_hour: int = Field(10, ge=0, le=1000)
    retry_verify_timeout_minutes: int = Field(30, ge=1, le=1440)
    no_retry_dags: tuple[str, ...] = ()


class ApprovalSettings(_Section):
    trusted_approvers: tuple[str, ...] = ()
    comment_command: str = "/approve"


class MCPSettings(_Section):
    allow_airflow_writes: bool = False
    allow_linear_writes: bool = False


class OwnershipSettings(_Section):
    user_map: dict[str, str] = Field(default_factory=dict)


class StorageSettings(_Section):
    database_path: Path = Path("./data/oncall.db")


class LoggingSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["text", "json"] = "text"
    dir: Path = Path("./logs")

    @field_validator("level", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v


class Settings(_Section):
    app_env: AppEnv = "development"
    integration_mode: IntegrationMode = "mock"
    ssm_prefix: str | None = None
    airflow: AirflowSettings = Field(default_factory=AirflowSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    linear: LinearSettings = Field(default_factory=LinearSettings)
    ai: AISettings = Field(default_factory=AISettings)
    polling: PollingSettings = Field(default_factory=PollingSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    ownership: OwnershipSettings = Field(default_factory=OwnershipSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    # Problems noticed while loading (e.g. unknown ONCALL_* variables). Reported by
    # validate_settings() as warnings.
    load_warnings: tuple[str, ...] = ()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_mocks(self) -> bool:
        return self.integration_mode == "mock"

    @property
    def auto_retries_enabled(self) -> bool:
        """FR-051: ONCALL_MAX_AUTO_RETRIES=0 (or a zero hourly limit) disables retries."""
        return self.policy.max_auto_retries > 0 and self.policy.global_max_retries_per_hour > 0

    def secret_values(self) -> list[str]:
        """All configured secret values, for log redaction (NFR-009)."""
        secrets = [
            self.airflow.password,
            self.airflow.token,
            self.github.token,
            self.linear.api_key,
            self.ai.groq_api_key,
        ]
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]


# ============================================================================ env mapping

# Environment variable -> path inside the settings tree.
ENV_MAP: dict[str, tuple[str, ...]] = {
    "APP_ENV": ("app_env",),
    "ONCALL_INTEGRATION_MODE": ("integration_mode",),
    "ONCALL_SSM_PREFIX": ("ssm_prefix",),
    "AIRFLOW_BASE_URL": ("airflow", "base_url"),
    "AIRFLOW_USERNAME": ("airflow", "username"),
    "AIRFLOW_PASSWORD": ("airflow", "password"),
    "AIRFLOW_TOKEN": ("airflow", "token"),
    "GITHUB_TOKEN": ("github", "token"),
    "GITHUB_REPOSITORY": ("github", "repository"),
    "LINEAR_API_KEY": ("linear", "api_key"),
    "LINEAR_TEAM_ID": ("linear", "team_id"),
    "LINEAR_PROJECT_ID": ("linear", "project_id"),
    "LINEAR_DEFAULT_ASSIGNEE": ("linear", "default_assignee"),
    "AI_PROVIDER": ("ai", "provider"),
    "GROQ_API_KEY": ("ai", "groq_api_key"),
    "GROQ_MODEL": ("ai", "groq_model"),
    "GROQ_BASE_URL": ("ai", "groq_base_url"),
    "POLL_INTERVAL_SECONDS": ("polling", "interval_seconds"),
    "ONCALL_MAX_AUTO_RETRIES": ("policy", "max_auto_retries"),
    "ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR": ("policy", "global_max_retries_per_hour"),
    "ONCALL_RETRY_VERIFY_TIMEOUT_MINUTES": ("policy", "retry_verify_timeout_minutes"),
    "ONCALL_TRUSTED_APPROVERS": ("approval", "trusted_approvers"),
    "ONCALL_MCP_ALLOW_AIRFLOW_WRITES": ("mcp", "allow_airflow_writes"),
    "ONCALL_MCP_ALLOW_LINEAR_WRITES": ("mcp", "allow_linear_writes"),
    "ONCALL_DATABASE_PATH": ("storage", "database_path"),
    "ONCALL_LOG_LEVEL": ("logging", "level"),
    "ONCALL_LOG_FORMAT": ("logging", "format"),
    "ONCALL_LOG_DIR": ("logging", "dir"),
}

# Variables read by the loader itself rather than mapped into Settings.
LOADER_ENV = {"ONCALL_CONFIG_DIR", "ONCALL_ENV_FILE"}

# Comma-separated list variables.
LIST_ENV = {"ONCALL_TRUSTED_APPROVERS"}

# Secrets that may be fetched from SSM in production (NFR-008).
SECRET_ENV = ("AIRFLOW_PASSWORD", "AIRFLOW_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "GROQ_API_KEY")

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Returns {parameter_name: value} for the names it finds; missing names are omitted.
SSMFetcher = Callable[[list[str]], dict[str, str]]


# ============================================================================ loading helpers


def _strip_comments(node: Any) -> Any:
    """Remove "_comment" keys used for documentation inside the JSON config files."""
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if k != "_comment"}
    if isinstance(node, list):
        return [_strip_comments(v) for v in node]
    return node


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object")
    return _strip_comments(data)


def _set_path(tree: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = tree
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _non_empty(env: Mapping[str, str | None]) -> dict[str, str]:
    return {k: v.strip() for k, v in env.items() if v is not None and v.strip() != ""}


def boto3_ssm_fetcher(region: str | None = None) -> SSMFetcher:
    """Build an SSM fetcher backed by boto3 (only imported when actually used)."""
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ConfigError("ONCALL_SSM_PREFIX is set but boto3 is not installed (pip install .[aws])") from exc

    client = boto3.client("ssm", region_name=region) if region else boto3.client("ssm")

    def fetch(names: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        # GetParameters accepts at most 10 names per call.
        for i in range(0, len(names), 10):
            try:
                resp = client.get_parameters(Names=names[i : i + 10], WithDecryption=True)
            except Exception as exc:  # botocore raises many types; all are fatal here
                raise SecretStoreError(f"SSM GetParameters failed: {type(exc).__name__}") from exc
            for param in resp.get("Parameters", []):
                found[param["Name"]] = param["Value"]
        return found

    return fetch


# ============================================================================ public API


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    config_dir: Path | None = None,
    env_file: Path | None = None,
    ssm_fetcher: SSMFetcher | None = None,
    validate: bool = True,
) -> Settings:
    """Load settings from env, .env, SSM and JSON. See the module docstring for precedence.

    ``env`` defaults to ``os.environ``. ``env_file`` defaults to ``<project>/.env``;
    pass a non-existent path to skip it. ``ssm_fetcher`` is injectable for tests.
    """
    raw_env = dict(os.environ) if env is None else dict(env)
    process_env = _non_empty(raw_env)

    if env_file is None:
        env_file = Path(process_env.get("ONCALL_ENV_FILE", PROJECT_ROOT / ".env"))
    file_env = _non_empty(dotenv_values(env_file)) if env_file.is_file() else {}
    merged_env = {**file_env, **process_env}  # real environment wins over .env

    if config_dir is None:
        config_dir = Path(merged_env.get("ONCALL_CONFIG_DIR", PROJECT_ROOT / "config"))
    base_dir = config_dir.resolve().parent  # relative paths resolve against the project root

    app_env = merged_env.get("APP_ENV", "development")
    if app_env not in ("development", "test", "production"):
        raise ConfigError("Invalid configuration", [ConfigIssue("error", "APP_ENV", f"must be development, test or production, got '{app_env}'")])

    if not (config_dir / "default.json").is_file():
        # Fail closed: without default.json, labels, priorities and no-retry DAGs would
        # silently fall back to empty values.
        raise ConfigError(
            "Configuration directory not found",
            [ConfigIssue("error", "ONCALL_CONFIG_DIR", f"{config_dir / 'default.json'} does not exist; set ONCALL_CONFIG_DIR to the project's config/ directory")],
        )
    tree = _read_json(config_dir / "default.json")
    tree = _deep_merge(tree, _read_json(config_dir / f"{app_env}.json"))

    # SSM: only fills secrets that the environment did not already provide.
    ssm_prefix = merged_env.get("ONCALL_SSM_PREFIX")
    if ssm_prefix:
        missing = [name for name in SECRET_ENV if name not in merged_env]
        if missing:
            fetcher = ssm_fetcher or boto3_ssm_fetcher()
            try:
                found = fetcher([ssm_prefix + name for name in missing])
            except SecretStoreError as exc:
                # Fail closed: never start with partially loaded production secrets.
                raise ConfigError("Cannot load secrets from SSM", [ConfigIssue("error", "ONCALL_SSM_PREFIX", str(exc))]) from exc
            for name in missing:
                value = found.get(ssm_prefix + name)
                if value:
                    merged_env[name] = value
            log.info("Loaded %d secret(s) from SSM prefix %s", sum(1 for n in missing if n in merged_env), ssm_prefix)

    warnings: list[str] = []
    for name, value in merged_env.items():
        if name in ENV_MAP:
            parsed: Any = value
            if name in LIST_ENV:
                parsed = [item.strip() for item in value.split(",") if item.strip()]
            _set_path(tree, ENV_MAP[name], parsed)
        elif name.startswith("ONCALL_") and name not in LOADER_ENV:
            # A typo such as ONCALL_MAX_AUTO_RETRY would otherwise be ignored silently.
            warnings.append(f"Unknown variable {name} is ignored")

    tree["app_env"] = app_env
    # Decision A5: env var, else config/<APP_ENV>.json, else mock only outside production.
    tree.setdefault("integration_mode", "real" if app_env == "production" else "mock")
    tree["load_warnings"] = warnings

    try:
        settings = Settings.model_validate(tree)
    except ValidationError as exc:
        issues = [
            ConfigIssue("error", ".".join(str(p) for p in err["loc"]), err["msg"]) for err in exc.errors()
        ]
        raise ConfigError("Invalid configuration", issues) from None  # hide values from traceback

    settings = _resolve_paths(settings, base_dir)

    if validate:
        issues = validate_settings(settings)
        if any(i.severity == "error" for i in issues):
            raise ConfigError("Invalid configuration", issues)
    return settings


def _resolve_paths(settings: Settings, base_dir: Path) -> Settings:
    db = settings.storage.database_path
    logs = settings.logging.dir
    return settings.model_copy(
        update={
            "storage": settings.storage.model_copy(update={"database_path": db if db.is_absolute() else (base_dir / db).resolve()}),
            "logging": settings.logging.model_copy(update={"dir": logs if logs.is_absolute() else (base_dir / logs).resolve()}),
        }
    )


def validate_settings(settings: Settings) -> list[ConfigIssue]:
    """Return every configuration problem (AT-002). Errors block startup; warnings do not."""
    issues: list[ConfigIssue] = []

    def error(key: str, msg: str) -> None:
        issues.append(ConfigIssue("error", key, msg))

    def warn(key: str, msg: str) -> None:
        issues.append(ConfigIssue("warning", key, msg))

    for message in settings.load_warnings:
        warn("environment", message)

    # --- Production safety (decision A5) -------------------------------------------
    if settings.is_production:
        if settings.use_mocks:
            error("ONCALL_INTEGRATION_MODE", "mock integrations are not allowed in production")
        if settings.ai.provider == "mock":
            error("AI_PROVIDER", "the mock AI provider is not allowed in production")
        if not settings.airflow.verify_tls:
            warn("airflow.verify_tls", "TLS verification is disabled in production")

    # --- Real integrations need real credentials -----------------------------------
    # Production always checks them, even if mock mode was (wrongly) requested, so
    # every missing credential is reported in one pass.
    if not settings.use_mocks or settings.is_production:
        af = settings.airflow
        if not af.base_url:
            error("AIRFLOW_BASE_URL", "required when integrations are real")
        elif not re.match(r"^https?://", af.base_url):
            error("AIRFLOW_BASE_URL", "must start with http:// or https://")
        elif af.base_url.endswith("/api/v2"):
            error("AIRFLOW_BASE_URL", "give the server URL without the /api/v2 suffix")
        elif settings.is_production and af.base_url.startswith("http://"):
            warn("AIRFLOW_BASE_URL", "plain HTTP in production sends credentials unencrypted")

        method = af.auth_method
        if method == "none":
            error("AIRFLOW_TOKEN", "set AIRFLOW_TOKEN, or AIRFLOW_USERNAME and AIRFLOW_PASSWORD")
        elif method == "conflict":
            error("AIRFLOW_TOKEN", "set either AIRFLOW_TOKEN or AIRFLOW_USERNAME/AIRFLOW_PASSWORD, not both")
        elif method == "incomplete":
            error("AIRFLOW_USERNAME", "AIRFLOW_USERNAME and AIRFLOW_PASSWORD must be set together")

        if settings.github.token is None:
            error("GITHUB_TOKEN", "required when integrations are real")
        if not settings.github.repository:
            error("GITHUB_REPOSITORY", "required when integrations are real")
        elif not _REPO_RE.match(settings.github.repository):
            error("GITHUB_REPOSITORY", "must look like owner/name")

        if settings.linear.api_key is None:
            error("LINEAR_API_KEY", "required when integrations are real")
        if not settings.linear.team_id:
            error("LINEAR_TEAM_ID", "required when integrations are real")

        if not settings.approval.trusted_approvers:
            warn("ONCALL_TRUSTED_APPROVERS", "empty: every approval of a restricted action will be rejected")
    else:
        # Mock mode: credentials are ignored. Say so, so nobody believes they are in use.
        configured = [
            name
            for name, value in (
                ("AIRFLOW_BASE_URL", settings.airflow.base_url),
                ("GITHUB_TOKEN", settings.github.token),
                ("LINEAR_API_KEY", settings.linear.api_key),
            )
            if value
        ]
        if configured:
            warn("ONCALL_INTEGRATION_MODE", f"mock mode: {', '.join(configured)} set but not used")

    # --- AI provider ---------------------------------------------------------------
    if settings.ai.provider == "groq":
        if settings.ai.groq_api_key is None:
            error("GROQ_API_KEY", "required when AI_PROVIDER=groq")
        if not settings.ai.groq_model:
            error("GROQ_MODEL", "required when AI_PROVIDER=groq (no built-in default)")
        if not re.match(r"^https?://", settings.ai.groq_base_url):
            error("GROQ_BASE_URL", "must start with http:// or https://")
        elif settings.is_production and settings.ai.groq_base_url.startswith("http://"):
            warn("GROQ_BASE_URL", "plain HTTP in production sends the API key unencrypted")

    # --- Policy --------------------------------------------------------------------
    if not settings.auto_retries_enabled:
        warn("ONCALL_MAX_AUTO_RETRIES", "automatic retries are disabled (a retry limit is 0)")

    return issues
