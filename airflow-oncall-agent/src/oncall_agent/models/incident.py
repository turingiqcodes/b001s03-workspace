"""Failure events detected in Airflow and the incidents they are grouped into (SRS §7.1)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis import AnalysisSource, Classification, ProposedAction

# Placeholder task IDs used in the fingerprint when a failure has no task (decision
# recorded in docs/architecture.md, "Fingerprint inputs").
DAG_RUN_TASK_ID = "__dag_run__"
IMPORT_ERROR_TASK_ID = "__import_error__"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FailureType(str, Enum):
    TASK_FAILURE = "task_failure"
    DAG_RUN_FAILURE = "dag_run_failure"
    IMPORT_ERROR = "import_error"


class IncidentStatus(str, Enum):
    """Incident lifecycle.

    open -> investigating -> {retrying, awaiting_approval, escalated} -> resolved.
    A resolved incident whose fingerprint fails again is reopened (decision A3).
    """

    OPEN = "open"
    INVESTIGATING = "investigating"
    RETRYING = "retrying"
    AWAITING_APPROVAL = "awaiting_approval"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


ACTIVE_STATUSES = frozenset(s for s in IncidentStatus if s is not IncidentStatus.RESOLVED)


class FailureEvent(BaseModel):
    """One failure observed by the monitoring service (FR-005..FR-008).

    ``event_key`` uniquely identifies the observation, so re-reading the same failure
    in an overlapping poll window is recognised as a duplicate (NFR-005).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_type: FailureType
    dag_id: str = Field(min_length=1)
    dag_run_id: str | None = None
    task_id: str | None = None
    try_number: int | None = Field(default=None, ge=0)
    map_index: int = -1
    state: str
    logical_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    # Import errors only: the DAG file path and Airflow's stack trace.
    filename: str | None = None
    error_message: str | None = None
    # DAG owner(s) as reported by Airflow, used by ownership resolution (FR-078).
    owners: tuple[str, ...] = ()
    detected_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _shape(self) -> "FailureEvent":
        if self.failure_type is FailureType.TASK_FAILURE and (not self.task_id or not self.dag_run_id):
            raise ValueError("task failures need dag_run_id and task_id")
        if self.failure_type is FailureType.DAG_RUN_FAILURE and not self.dag_run_id:
            raise ValueError("DAG run failures need dag_run_id")
        if self.failure_type is FailureType.IMPORT_ERROR and not self.filename:
            raise ValueError("import errors need filename")
        return self

    @property
    def fingerprint_task_id(self) -> str:
        """Task component of the fingerprint (FR-015)."""
        if self.failure_type is FailureType.TASK_FAILURE:
            return self.task_id or ""
        if self.failure_type is FailureType.DAG_RUN_FAILURE:
            return DAG_RUN_TASK_ID
        return IMPORT_ERROR_TASK_ID

    @property
    def event_key(self) -> str:
        """Stable identity of this observation, used for idempotent processing."""
        if self.failure_type is FailureType.TASK_FAILURE:
            return f"task:{self.dag_id}:{self.dag_run_id}:{self.task_id}:{self.map_index}:{self.try_number}"
        if self.failure_type is FailureType.DAG_RUN_FAILURE:
            return f"dag_run:{self.dag_id}:{self.dag_run_id}"
        stamp = self.end_date.isoformat() if self.end_date else ""
        return f"import:{self.filename}:{stamp}"


class Incident(BaseModel):
    """A group of failures sharing one fingerprint (FR-016/017), mirrored to Linear."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: int | None = None
    fingerprint: str = Field(min_length=64, max_length=64)
    # Exact inputs to the hash, kept for debugging and audit (FR-020).
    fingerprint_input: dict[str, Any]
    failure_type: FailureType
    dag_id: str
    task_id: str | None = None
    dag_run_id: str | None = None
    # FR-019: original and normalized error kept separately.
    original_error: str | None = None
    normalized_error: str

    classification: Classification | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    root_cause: str | None = None
    evidence: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    proposed_action: ProposedAction | None = None
    analysis_source: AnalysisSource | None = None
    suspected_commit: str | None = None

    status: IncidentStatus = IncidentStatus.OPEN
    linear_issue_id: str | None = None
    linear_issue_identifier: str | None = None
    assignee: str | None = None
    ownership_note: str | None = None

    first_detected_at: datetime = Field(default_factory=utcnow)
    last_detected_at: datetime = Field(default_factory=utcnow)
    occurrence_count: int = Field(1, ge=1)
    # Lifetime count of automatic retries (SRS §7.1 "Retry count").
    retry_count: int = Field(0, ge=0)
    # Automatic retries since the incident was opened or last reopened. This is the
    # counter the FR-050 "fewer than N retries" rule uses (decision A3).
    cycle_retry_count: int = Field(0, ge=0)
    reopened_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # Why the incident is in its current status (shown in Linear comments).
    status_reason: str | None = None
    # Analysis and context details kept for reporting (Linear, CLI) after a restart.
    analysis_explanation: str | None = None
    analysis_fallback_reason: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    context_summary: dict[str, Any] = Field(default_factory=dict)
    # What was last mirrored to Linear; the reconcile loop pushes any difference.
    linear_synced_status: str | None = None
    linear_synced_occurrences: int = Field(0, ge=0)

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES
