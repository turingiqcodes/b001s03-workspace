"""Audit events (SRS §7.5, NFR-025..NFR-029)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEventType(str, Enum):
    # Monitoring
    POLL_COMPLETED = "poll_completed"
    POLL_FAILED = "poll_failed"
    FAILURE_DETECTED = "failure_detected"
    # Incident lifecycle (NFR-026)
    INCIDENT_CREATED = "incident_created"
    INCIDENT_UPDATED = "incident_updated"
    INCIDENT_REOPENED = "incident_reopened"
    INCIDENT_ESCALATED = "incident_escalated"
    INCIDENT_RESOLVED = "incident_resolved"
    # Investigation and analysis
    CONTEXT_COLLECTED = "context_collected"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_FALLBACK = "analysis_fallback"
    # Policy (NFR-027, FR-053)
    POLICY_DECISION = "policy_decision"
    # Recovery (NFR-029, FR-064)
    RETRY_STARTED = "retry_started"
    RETRY_SUCCEEDED = "retry_succeeded"
    RETRY_FAILED = "retry_failed"
    RETRY_TIMED_OUT = "retry_timed_out"
    RETRY_BLOCKED = "retry_blocked"
    # Approvals (FR-072)
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RECEIVED = "approval_received"
    APPROVAL_REJECTED = "approval_rejected"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    # Linear
    LINEAR_ISSUE_CREATED = "linear_issue_created"
    LINEAR_ISSUE_UPDATED = "linear_issue_updated"
    OWNERSHIP_RESOLVED = "ownership_resolved"
    # Integrations and MCP (NFR-028, §8.3)
    INTEGRATION_FAILURE = "integration_failure"
    MCP_TOOL_CALL = "mcp_tool_call"


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    incident_id: int | None = None
    event_type: AuditEventType
    # "agent", "mcp:<server>", "linear:<user>", "cli", "system"
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    input_summary: str | None = None
    output_summary: str | None = None
    success: bool = True
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
