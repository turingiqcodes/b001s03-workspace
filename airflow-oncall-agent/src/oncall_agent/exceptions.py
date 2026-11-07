"""Exception hierarchy for the Airflow Oncall Agent (NFR-022).

Every error raised on purpose by the agent derives from :class:`OncallError`, so callers
can tell agent errors apart from programming bugs. Integration errors carry a
``retriable`` flag that callers use to decide between "try again next cycle" and
"record and move on"; it never authorises an Airflow retry by itself.
"""
from __future__ import annotations

from dataclasses import dataclass


class OncallError(Exception):
    """Base class for all agent errors."""


# ---------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class ConfigIssue:
    """One configuration problem. ``severity`` is "error" (blocks startup) or "warning"."""

    severity: str
    key: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.severity}] {self.key}: {self.message}"


class ConfigError(OncallError):
    """Configuration is missing, malformed or unsafe. The agent must not start."""

    def __init__(self, message: str, issues: list[ConfigIssue] | None = None) -> None:
        self.issues = list(issues or [])
        detail = "; ".join(f"{i.key}: {i.message}" for i in self.issues if i.severity == "error")
        super().__init__(f"{message}: {detail}" if detail else message)


# ---------------------------------------------------------------------------- integrations


class IntegrationError(OncallError):
    """An external system (Airflow, GitHub, Linear, AI provider, SSM) call failed.

    ``retriable`` means the *API call* may succeed if repeated later (timeouts, 5xx,
    rate limits). It says nothing about whether an Airflow task may be retried.
    """

    service = "integration"

    def __init__(self, message: str, *, retriable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retriable = retriable
        self.status_code = status_code


class AirflowError(IntegrationError):
    service = "airflow"


class GitHubError(IntegrationError):
    service = "github"


class LinearError(IntegrationError):
    service = "linear"


class SecretStoreError(IntegrationError):
    service = "ssm"


class MalformedResponseError(IntegrationError):
    """An external API returned data that does not match the expected shape (NFR-010)."""

    def __init__(self, service: str, message: str) -> None:
        super().__init__(f"{service}: malformed response: {message}", retriable=False)
        self.service = service


# ---------------------------------------------------------------------------- AI analysis


class AIProviderError(OncallError):
    """The AI provider could not produce an analysis (unavailable, timeout, refused)."""


class InvalidAIResponseError(AIProviderError):
    """The AI provider answered, but the answer failed schema validation (FR-038/039)."""


# ---------------------------------------------------------------------------- persistence


class StoreError(OncallError):
    """A SQLite operation failed; the surrounding transaction was rolled back."""


# ---------------------------------------------------------------------------- safety


class SafetyError(OncallError):
    """A safety rule blocked an operation. Always fail closed when this is raised."""


class PolicyDeniedError(SafetyError):
    """The deterministic policy engine refused the requested operation."""


class UnsafeClearError(SafetyError):
    """A task clear would have touched successful or unrelated task instances (FR-058/059)."""


class ApprovalError(SafetyError):
    """An approval is missing, invalid, from an untrusted user, or already used."""


class DuplicateExecutionError(SafetyError):
    """The same retry or approved action was already executed (FR-065/070)."""


class WriteDisabledError(SafetyError):
    """A write operation was requested through an interface where writes are disabled."""
