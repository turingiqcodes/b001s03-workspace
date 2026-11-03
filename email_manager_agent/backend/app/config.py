"""Application configuration, read from environment variables (and an optional .env file).

Every numeric limit here is documented in docs/REQUIREMENTS_CHECKLIST.md. The SRS v1.0
was not available when this was built, so values marked "assumed" there should be
checked against the SRS and changed here (or in .env) if they differ.
"""
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives in the project root (email_manager_agent/.env), one level above backend/.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Language model ---
    llm_provider: Literal["ollama", "claude"] = "ollama"

    # --- Ollama (local models; used when LLM_PROVIDER=ollama) ---
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None  # required; must support tool calling, e.g. qwen3:8b
    ollama_num_ctx: int = Field(16384, ge=2048, le=262144)  # context window; Ollama's default is too small
    ollama_timeout_seconds: float = Field(600, gt=0)  # local models on CPU can be slow
    # How long Ollama keeps the model loaded between requests. Longer = it keeps the
    # already-processed instructions and tool list cached, so the next request starts faster.
    ollama_keep_alive: str = "30m"
    ollama_think: bool | None = None  # only for thinking models; unset = model default

    # --- Claude (used when LLM_PROVIDER=claude) ---
    anthropic_api_key: SecretStr | None = None
    claude_model: str | None = None  # required; no built-in default on purpose
    claude_max_tokens: int = Field(16000, ge=1024, le=64000)
    claude_effort: str = "medium"  # low | medium | high | xhigh | max
    claude_refusal_fallback: bool = True  # server-side fallback when a request is declined
    claude_timeout_seconds: float = Field(120, gt=0)

    # --- Apps Script bridge ---
    apps_script_url: str | None = None
    apps_script_shared_secret: SecretStr | None = None
    bridge_timeout_seconds: float = Field(30, gt=0, le=120)

    # --- Server ---
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]

    # --- Agent and session limits ---
    max_agent_steps: int = Field(10, ge=1, le=50)
    prompt_max_chars: int = Field(4000, ge=1)
    tool_result_max_chars: int = Field(20000, ge=1000)
    approval_ttl_seconds: int = Field(600, ge=30)
    session_idle_ttl_seconds: int = Field(3600, ge=60)

    # --- Gmail limits ---
    email_search_max_results: int = Field(20, ge=1, le=50)
    email_body_max_chars: int = Field(10000, ge=500)
    outgoing_body_max_chars: int = Field(20000, ge=500)
    subject_max_chars: int = Field(250, ge=10)
    max_recipients: int = Field(20, ge=1)

    # --- Calendar ---
    user_timezone: str = "UTC"
    work_day_start: time = time(9, 0)
    work_day_end: time = time(17, 0)
    work_days: list[int] = [0, 1, 2, 3, 4]  # Monday=0 ... Sunday=6
    slot_grid_minutes: int = Field(30, ge=5, le=120)
    max_suggested_slots: int = Field(5, ge=1, le=20)
    availability_max_range_days: int = Field(14, ge=1, le=60)
    calendar_list_max_results: int = Field(50, ge=1, le=250)

    # --- Drive ---
    attachment_folder_name: str = "Email Attachments"
    attachment_max_bytes: int = Field(10 * 1024 * 1024, ge=1024)
    supported_attachment_extensions: list[str] = [
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt",
        "png", "jpg", "jpeg", "gif",
    ]
    drive_search_max_results: int = Field(25, ge=1, le=100)

    @field_validator("user_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"USER_TIMEZONE '{value}' is not a valid IANA timezone") from exc
        return value

    @field_validator("claude_effort")
    @classmethod
    def _check_effort(cls, value: str) -> str:
        allowed = {"low", "medium", "high", "xhigh", "max"}
        if value not in allowed:
            raise ValueError(f"CLAUDE_EFFORT must be one of {sorted(allowed)}")
        return value

    @field_validator("supported_attachment_extensions")
    @classmethod
    def _normalise_extensions(cls, value: list[str]) -> list[str]:
        return [ext.lower().lstrip(".") for ext in value]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.user_timezone)

    @property
    def active_model(self) -> str | None:
        return self.ollama_model if self.llm_provider == "ollama" else self.claude_model

    def llm_config_issues(self) -> list[str]:
        """Human-readable problems that stop the agent from calling the configured model."""
        if self.llm_provider == "ollama":
            return self.ollama_config_issues()
        return self.claude_config_issues()

    def ollama_config_issues(self) -> list[str]:
        issues = []
        if not self.ollama_model or not self.ollama_model.strip():
            issues.append("OLLAMA_MODEL is not set (for example OLLAMA_MODEL=qwen3:8b).")
        if not self.ollama_base_url.startswith(("http://", "https://")):
            issues.append("OLLAMA_BASE_URL must start with http:// or https://.")
        return issues

    def claude_config_issues(self) -> list[str]:
        """Human-readable problems that stop the agent from calling Claude."""
        issues = []
        if not self.anthropic_api_key or not self.anthropic_api_key.get_secret_value():
            issues.append("ANTHROPIC_API_KEY is not set.")
        if not self.claude_model or not self.claude_model.strip():
            issues.append("CLAUDE_MODEL is not set (for example CLAUDE_MODEL=claude-opus-5-5).")
        return issues

    def bridge_config_issues(self) -> list[str]:
        """Human-readable problems that stop the backend from reaching Google."""
        issues = []
        if not self.apps_script_url:
            issues.append("APPS_SCRIPT_URL is not set. Deploy apps_script/ and paste the Web App URL.")
        elif not self.apps_script_url.startswith("https://script.google.com/"):
            issues.append("APPS_SCRIPT_URL must be a https://script.google.com/... Web App URL.")
        if not self.apps_script_shared_secret or not self.apps_script_shared_secret.get_secret_value():
            issues.append("APPS_SCRIPT_SHARED_SECRET is not set.")
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
