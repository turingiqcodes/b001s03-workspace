"""Configuration loading and validation.

Settings come from environment variables, optionally seeded from a `.env` file.
Secrets are held in memory only and are excluded from ``repr`` so they never
end up in logs or tracebacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from dotenv import load_dotenv

Transport = Literal["stdio", "http"]

DEFAULT_API_URL = "https://api.github.com"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised when settings are missing or invalid. Messages never contain secret values."""


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: str
    installation_id: str
    private_key: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    api_url: str = DEFAULT_API_URL
    token: str | None = field(default=None, repr=False)
    app: GitHubAppCredentials | None = field(default=None, repr=False)
    timeout: float = 30.0
    max_retries: int = 3
    max_rate_limit_wait: float = 60.0
    max_text_chars: int = 20_000
    dry_run: bool = False
    per_request_auth: bool = False
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def auth_method(self) -> Literal["pat", "github_app", "none"]:
        """PAT takes precedence over GitHub App when both are configured."""
        if self.token:
            return "pat"
        if self.app:
            return "github_app"
        return "none"

    @property
    def has_server_credentials(self) -> bool:
        return self.auth_method != "none"

    def validate_startup(self) -> None:
        """Fail fast with a clear message if the server cannot authenticate."""
        if self.has_server_credentials:
            return
        if self.transport == "http" and self.per_request_auth:
            return
        hint = (
            "Set GITHUB_TOKEN (personal access token), or GITHUB_APP_ID + "
            "GITHUB_APP_INSTALLATION_ID + GITHUB_APP_PRIVATE_KEY / GITHUB_APP_PRIVATE_KEY_PATH."
        )
        if self.transport == "http":
            hint += " Alternatively set GITHUB_PER_REQUEST_AUTH=true so HTTP clients supply X-GitHub-Token."
        raise ConfigError(f"No GitHub credentials configured. {hint}")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        dotenv_path: str | os.PathLike[str] | None = None,
        load_dotenv_file: bool = True,
    ) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ`` after loading ``.env``)."""
        if env is None:
            if load_dotenv_file:
                load_dotenv(dotenv_path=dotenv_path, override=False)
            env = os.environ
        return _parse(env)


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _number(env: Mapping[str, str], name: str, default, cast, minimum, maximum=None):
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ConfigError(f"{name} must be {bounds}, got {value}")
    return value


def _load_private_key(env: Mapping[str, str]) -> str | None:
    inline = _get(env, "GITHUB_APP_PRIVATE_KEY")
    path = _get(env, "GITHUB_APP_PRIVATE_KEY_PATH")
    if inline:
        # Allow single-line values with escaped newlines, as is common in .env files.
        return inline.replace("\\n", "\n")
    if path:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(
                f"GITHUB_APP_PRIVATE_KEY_PATH could not be read ({exc.__class__.__name__}): {path}"
            ) from None
    return None


def _validate_private_key(pem: str) -> None:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(pem.encode(), password=None)
    except Exception:
        raise ConfigError("GitHub App private key is not a valid unencrypted PEM private key") from None


def _parse(env: Mapping[str, str]) -> Settings:
    token = _get(env, "GITHUB_TOKEN") or _get(env, "GITHUB_PERSONAL_ACCESS_TOKEN")

    app_id = _get(env, "GITHUB_APP_ID")
    installation_id = _get(env, "GITHUB_APP_INSTALLATION_ID")
    has_key_setting = bool(_get(env, "GITHUB_APP_PRIVATE_KEY") or _get(env, "GITHUB_APP_PRIVATE_KEY_PATH"))
    app: GitHubAppCredentials | None = None
    if app_id or installation_id or has_key_setting:
        missing = [
            name
            for name, present in (
                ("GITHUB_APP_ID", app_id),
                ("GITHUB_APP_INSTALLATION_ID", installation_id),
                ("GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH", has_key_setting),
            )
            if not present
        ]
        if missing and not token:
            raise ConfigError("Incomplete GitHub App configuration; missing: " + ", ".join(missing))
        if not missing:
            # When a PAT is set it wins, but a broken App config is still reported
            # only if the App would actually be used.
            key = _load_private_key(env) if not token else None
            if key is not None:
                _validate_private_key(key)
                app = GitHubAppCredentials(app_id=app_id, installation_id=installation_id, private_key=key)

    transport = (_get(env, "MCP_TRANSPORT") or "stdio").lower()
    if transport in {"streamable-http", "streamable_http"}:
        transport = "http"
    if transport not in {"stdio", "http"}:
        raise ConfigError(f"MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}")

    api_url = (_get(env, "GITHUB_API_URL") or DEFAULT_API_URL).rstrip("/")
    if not api_url.startswith(("https://", "http://")):
        raise ConfigError(f"GITHUB_API_URL must be an http(s) URL, got {api_url!r}")

    return Settings(
        api_url=api_url,
        token=token,
        app=app,
        timeout=_number(env, "GITHUB_TIMEOUT", 30.0, float, 0.1),
        max_retries=_number(env, "GITHUB_MAX_RETRIES", 3, int, 0, 10),
        max_rate_limit_wait=_number(env, "GITHUB_MAX_RATE_LIMIT_WAIT", 60.0, float, 0),
        max_text_chars=_number(env, "GITHUB_MAX_TEXT_CHARS", 20_000, int, 100),
        dry_run=_bool(env, "GITHUB_DRY_RUN", False),
        per_request_auth=_bool(env, "GITHUB_PER_REQUEST_AUTH", False),
        transport=transport,  # type: ignore[arg-type]
        host=_get(env, "MCP_HOST") or "127.0.0.1",
        port=_number(env, "MCP_PORT", 8000, int, 1, 65535),
        log_level=(_get(env, "LOG_LEVEL") or "INFO").upper(),
    )
