"""Domain models shared by services, integrations and persistence."""
from .analysis import (
    FALLBACK_MAX_CONFIDENCE,
    RESTRICTED_ACTIONS,
    AIAnalysisPayload,
    AnalysisResult,
    AnalysisSource,
    Classification,
    ProposedAction,
)
from .approval import ApprovalRecord, ApprovalSource, ApprovalStatus
from .events import AuditEvent, AuditEventType
from .incident import (
    DAG_RUN_TASK_ID,
    IMPORT_ERROR_TASK_ID,
    FailureEvent,
    FailureType,
    Incident,
    IncidentStatus,
)

__all__ = [
    "AIAnalysisPayload",
    "AnalysisResult",
    "AnalysisSource",
    "ApprovalRecord",
    "ApprovalSource",
    "ApprovalStatus",
    "AuditEvent",
    "AuditEventType",
    "Classification",
    "DAG_RUN_TASK_ID",
    "FALLBACK_MAX_CONFIDENCE",
    "FailureEvent",
    "FailureType",
    "IMPORT_ERROR_TASK_ID",
    "Incident",
    "IncidentStatus",
    "ProposedAction",
    "RESTRICTED_ACTIONS",
]
