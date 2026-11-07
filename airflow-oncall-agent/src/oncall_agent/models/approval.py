"""Human approval records for restricted actions (SRS §7.4, FR-066..FR-072).

One record is created when the agent *requests* approval for a restricted action.
It moves through the statuses below exactly once; the store enforces the transitions
inside transactions so an approved action can execute only once (FR-070).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .analysis import RESTRICTED_ACTIONS, ProposedAction


class ApprovalSource(str, Enum):
    LINEAR_LABEL = "linear_label"  # FR-068: label "oncall:approved"
    LINEAR_COMMENT = "linear_comment"  # FR-069: comment "/approve"


class ApprovalStatus(str, Enum):
    """pending -> approved -> executing -> executed | failed;  pending -> rejected."""

    PENDING = "pending"  # requested, waiting for a trusted human
    APPROVED = "approved"  # valid approval received, not yet executed
    EXECUTING = "executing"  # claimed for execution (guards against double execution)
    EXECUTED = "executed"
    FAILED = "failed"  # execution attempted and failed; never re-run automatically
    REJECTED = "rejected"  # cancelled, e.g. incident resolved before approval


TERMINAL_APPROVAL_STATUSES = frozenset({ApprovalStatus.EXECUTED, ApprovalStatus.FAILED, ApprovalStatus.REJECTED})


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: int | None = None
    incident_id: int
    requested_action: ProposedAction
    # The exact target of the action. An incident can span many DAG runs, so the
    # approval is bound to the run (and task, for mark_success) it was requested for.
    dag_id: str
    dag_run_id: str
    task_id: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApprovalStatus = ApprovalStatus.PENDING
    approval_source: ApprovalSource | None = None
    approver: str | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    execution_result: str | None = None

    @field_validator("requested_action")
    @classmethod
    def _restricted_only(cls, v: ProposedAction) -> ProposedAction:
        # Only restricted actions go through approval; retry_task is decided by policy
        # and is escalated (not approvable) when policy declines it (decision A2).
        if v not in RESTRICTED_ACTIONS:
            raise ValueError(f"approval can only be requested for {sorted(a.value for a in RESTRICTED_ACTIONS)}")
        return v
