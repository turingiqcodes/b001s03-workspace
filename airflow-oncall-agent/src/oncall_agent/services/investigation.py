"""Investigation step of the incident lifecycle (FR-021..029, NFR-028).

Moves the incident to ``investigating``, collects diagnostic context and audits what
was collected and what was missing. The resulting :class:`InvestigationContext` is
handed to the analysis service (Phase 5); it is not persisted as a whole — its summary
is in the audit trail and the analysis result is stored on the incident.
"""
from __future__ import annotations

import logging

from ..context import ContextCollector, InvestigationContext
from ..models import AuditEventType, Incident, IncidentStatus
from ..store import Store
from .audit import AuditService

log = logging.getLogger(__name__)


class InvestigationService:
    def __init__(self, collector: ContextCollector, store: Store, audit: AuditService) -> None:
        self.collector = collector
        self.store = store
        self.audit = audit

    def investigate(self, incident: Incident, *, known_log: str | None = None) -> InvestigationContext:
        if incident.id is not None and incident.status is IncidentStatus.OPEN:
            current = self.store.get_incident(incident.id) or incident
            if current.status is IncidentStatus.OPEN:
                incident = self.store.update_incident(current.model_copy(update={"status": IncidentStatus.INVESTIGATING}))

        ctx = self.collector.collect(incident, known_log=known_log)

        for action, message in self.collector.integration_failures:
            self.audit.record(
                AuditEventType.INTEGRATION_FAILURE,
                actor="agent",
                action=action,
                incident_id=incident.id,
                output_summary=message,
                success=False,
                details={"stage": "context_collection", "effect": "recorded as missing evidence"},
            )
        self.audit.record(
            AuditEventType.CONTEXT_COLLECTED,
            actor="agent",
            action="collect_context",
            incident_id=incident.id,
            input_summary={"dag_id": ctx.dag_id, "task_id": ctx.task_id, "dag_run_id": ctx.dag_run_id},
            output_summary=ctx.summary(),
            success=True,
            details={"observations": ctx.observations},
        )
        log.info("Context for incident %s: %s", incident.id, ctx.summary())
        return ctx
