"""Incident lifecycle (FR-016..FR-018, NFR-005, NFR-026; decision A3).

Phase 3 scope: turning a detected failure into exactly one incident row.
:meth:`IncidentManager.record_failure` runs in ONE transaction:

* the observation (``FailureEvent.event_key``) was already processed → nothing changes;
* no incident has this fingerprint → a new ``open`` incident is created;
* an active incident has it → occurrence count and "last detected" are updated;
* a resolved incident has it → it is **reopened**: status ``open``, ``reopened_at``
  set, ``cycle_retry_count`` reset to 0 (``retry_count`` keeps the lifetime total).

:class:`LinearIncidentService` (below) mirrors incidents to Linear (FR-074..078).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..exceptions import OncallError
from ..fingerprint import Fingerprint
from ..models import AuditEventType, FailureEvent, FailureType, Incident, IncidentStatus
from ..store import Store
from .audit import AuditService

log = logging.getLogger(__name__)

MAX_ORIGINAL_ERROR_CHARS = 4000

STALE_ANALYSIS_RESET: dict[str, object] = {
    "classification": None, "confidence": None, "root_cause": None, "evidence": [], "suggested_fix": None,
    "proposed_action": None, "analysis_source": None, "suspected_commit": None,
    "analysis_explanation": None, "analysis_fallback_reason": None, "missing_evidence": [],
}  # fmt: skip


class RecordOutcome(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"  # same fingerprint, incident still active (FR-017)
    REOPENED = "reopened"  # same fingerprint, incident had been resolved
    ALREADY_PROCESSED = "already_processed"  # same observation seen before (overlap window)


@dataclass(frozen=True)
class RecordResult:
    outcome: RecordOutcome
    incident: Incident | None

    @property
    def needs_investigation(self) -> bool:
        """New or reopened incidents are investigated; repeats of an active incident are
        only counted, so one flapping task does not trigger repeated AI calls."""
        return self.outcome in (RecordOutcome.CREATED, RecordOutcome.REOPENED)


class IncidentManager:
    def __init__(self, store: Store, audit: AuditService, *, clock=None) -> None:
        self.store = store
        self.audit = audit
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def record_failure(self, event: FailureEvent, fp: Fingerprint) -> RecordResult:
        """Create, update or reopen the incident for ``event``. Idempotent per event."""
        now = self._clock()
        detected_at = event.end_date or event.detected_at
        original = fp.original_error_line[:MAX_ORIGINAL_ERROR_CHARS]

        with self.store.transaction():
            if self.store.is_event_processed(event.event_key):
                return RecordResult(RecordOutcome.ALREADY_PROCESSED, None)

            existing = self.store.get_incident_by_fingerprint(fp.value)
            if existing is None:
                incident = self.store.create_incident(
                    Incident(
                        fingerprint=fp.value,
                        fingerprint_input=fp.inputs(),
                        failure_type=event.failure_type,
                        dag_id=event.dag_id,
                        task_id=event.task_id,
                        dag_run_id=event.dag_run_id,
                        original_error=original,
                        normalized_error=fp.normalized_error_line,
                        status=IncidentStatus.OPEN,
                        first_detected_at=detected_at,
                        last_detected_at=detected_at,
                    )
                )
                outcome = RecordOutcome.CREATED
            else:
                update: dict[str, object] = {
                    "occurrence_count": existing.occurrence_count + 1,
                    "last_detected_at": max(existing.last_detected_at, detected_at),
                    "dag_run_id": event.dag_run_id or existing.dag_run_id,
                    "original_error": original,
                }
                if existing.status is IncidentStatus.RESOLVED:
                    # Reopen: fresh retry budget for this cycle (A3) and NO stale analysis —
                    # the new failure must be analysed before any decision is made.
                    update.update(status=IncidentStatus.OPEN, reopened_at=now, cycle_retry_count=0,
                                  status_reason="the failure occurred again after the incident was resolved", **STALE_ANALYSIS_RESET)
                    outcome = RecordOutcome.REOPENED
                else:
                    outcome = RecordOutcome.DUPLICATE
                incident = self.store.update_incident(existing.model_copy(update=update))

            self.store.mark_event_processed(event, incident.id)

        self._audit(outcome, incident, event, fp)
        return RecordResult(outcome, incident)

    def mark_event_without_incident(self, event: FailureEvent, reason: str) -> bool:
        """Record an observation that does not need its own incident (e.g. a failed DAG
        run whose failed tasks already have incidents) so it is not re-examined."""
        recorded = self.store.mark_event_processed(event, None)
        if recorded:
            log.debug("Event %s folded: %s", event.event_key, reason)
        return recorded

    def _audit(self, outcome: RecordOutcome, incident: Incident, event: FailureEvent, fp: Fingerprint) -> None:
        event_type = {
            RecordOutcome.CREATED: AuditEventType.INCIDENT_CREATED,
            RecordOutcome.DUPLICATE: AuditEventType.INCIDENT_UPDATED,
            RecordOutcome.REOPENED: AuditEventType.INCIDENT_REOPENED,
        }[outcome]
        self.audit.record(
            AuditEventType.FAILURE_DETECTED,
            actor="agent",
            action=f"detect_{event.failure_type.value}",
            incident_id=incident.id,
            input_summary={"event_key": event.event_key, "state": event.state, "try_number": event.try_number},
            output_summary=f"fingerprint {fp.value[:12]} ({outcome.value})",
        )
        self.audit.record(
            event_type,
            actor="agent",
            action=f"incident_{outcome.value}",
            incident_id=incident.id,
            input_summary={"dag_id": incident.dag_id, "task_id": incident.task_id, "dag_run_id": incident.dag_run_id},
            output_summary={"status": incident.status.value, "occurrences": incident.occurrence_count, "normalized_error": incident.normalized_error},
            details={"fingerprint_input": fp.inputs()} if outcome is RecordOutcome.CREATED else {},
        )


# =============================================================================== Linear (Phase 7)


@dataclass
class LinearSyncReport:
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    relinked: list[int] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class LinearIncidentService:
    """Mirrors incidents to Linear (FR-074..078, FR-018).

    Driven by :meth:`reconcile`, called every cycle: every analysed incident without an
    issue gets one, and every issue whose status/occurrences lag behind the incident is
    updated. A Linear failure is audited and simply retried next cycle (the local
    ``linear_synced_*`` markers only move after Linear accepted the change), so a Linear
    outage never loses an update and never blocks monitoring.

    Duplicate protection: before creating, the agent searches Linear for its own
    ``oncall-fingerprint: <sha>`` marker, so a lost database or a create that timed out
    after succeeding re-links the existing issue instead of creating a second one.
    """

    def __init__(self, linear, store: Store, audit: AuditService, settings, ownership) -> None:
        self.linear = linear
        self.store = store
        self.audit = audit
        self.settings = settings  # oncall_agent.config.Settings
        self.ownership = ownership
        self._label_ids: dict[str, str] | None = None
        self._state_ids: dict[str, str] | None = None

    # ----------------------------------------------------------------- lookups (cached)

    def _labels(self) -> dict[str, str]:
        if self._label_ids is None:
            lbl = self.settings.linear.labels
            names = [lbl.base, lbl.approved, lbl.import_error, lbl.awaiting_approval, lbl.escalated, *lbl.classification.values()]
            self._label_ids = self.linear.find_label_ids([n for n in dict.fromkeys(names) if n])
            missing = [n for n in names if n and n not in self._label_ids]
            if missing:
                log.warning("Linear labels not found (they are never created automatically): %s", ", ".join(missing))
        return self._label_ids

    def _state_id(self, incident: Incident) -> str | None:
        if self._state_ids is None:
            self._state_ids = {s.name: s.id for s in self.linear.list_workflow_states()}
        key = {IncidentStatus.RESOLVED: "resolved", IncidentStatus.RETRYING: "in_progress"}.get(incident.status, "open")
        name = self.settings.linear.states.get(key)
        state_id = self._state_ids.get(name) if name else None
        if name and state_id is None:
            log.warning("Linear workflow state %r not found in the team", name)
        return state_id

    def _desired_labels(self, incident: Incident) -> tuple[list[str], list[str]]:
        lbl = self.settings.linear.labels
        ids = self._labels()
        want = {lbl.base}
        if incident.classification is not None:
            want.add(lbl.classification.get(incident.classification.value, ""))
        if incident.failure_type is FailureType.IMPORT_ERROR:
            want.add(lbl.import_error)
        if incident.status is IncidentStatus.AWAITING_APPROVAL:
            want.add(lbl.awaiting_approval)
        if incident.status is IncidentStatus.ESCALATED:
            want.add(lbl.escalated)
        managed = {lbl.import_error, lbl.awaiting_approval, lbl.escalated, *lbl.classification.values()}
        add = sorted(ids[n] for n in want if n in ids)
        remove = sorted(ids[n] for n in managed - want if n in ids)
        return add, remove  # the approval label is never added or removed by the agent

    def _priority(self, incident: Incident) -> int | None:
        prio = self.settings.linear.priority
        if incident.failure_type is FailureType.IMPORT_ERROR:
            return prio.get("import_error")
        return prio.get(incident.classification.value) if incident.classification else None

    # ----------------------------------------------------------------- reconcile

    def reconcile(self) -> LinearSyncReport:
        report = LinearSyncReport()
        for incident in self.store.list_incidents_needing_linear_sync():
            try:
                self.sync(incident, report)
            except OncallError as exc:
                report.failed.append(f"incident {incident.id}: {exc}")
                self.audit.record(
                    AuditEventType.INTEGRATION_FAILURE, actor="agent", action="linear.sync", incident_id=incident.id,
                    output_summary=str(exc), success=False, details={"effect": "retried next cycle"},
                )  # fmt: skip
        return report

    def sync(self, incident: Incident, report: LinearSyncReport | None = None) -> Incident:
        report = report or LinearSyncReport()
        if incident.linear_issue_id is None:
            existing = self._find_existing(incident)
            if existing is not None:
                incident = self.store.update_incident(incident.model_copy(update={"linear_issue_id": existing.id, "linear_issue_identifier": existing.identifier}))
                report.relinked.append(incident.id)  # type: ignore[arg-type]
                self.audit.record(AuditEventType.LINEAR_ISSUE_UPDATED, actor="agent", action="relink_issue", incident_id=incident.id,
                                  output_summary=f"re-linked existing issue {existing.identifier} by fingerprint")  # fmt: skip
                return self._update(incident, report, force=True)
            return self._create(incident, report)
        return self._update(incident, report)

    def _find_existing(self, incident: Incident):
        from ..reporting import fingerprint_marker

        marker = fingerprint_marker(incident.fingerprint)
        for issue in self.linear.search_issues(marker, limit=5):
            if marker in (issue.description or ""):
                owner = self.store.get_incident_by_linear_issue(issue.id)
                if owner is None or owner.id == incident.id:
                    return issue
        return None

    def _create(self, incident: Incident, report: LinearSyncReport) -> Incident:
        from ..reporting import diagnostics_comment, issue_description, issue_title

        owner = self.ownership.resolve(incident)
        add, _ = self._desired_labels(incident)
        issue = self.linear.create_issue(
            title=issue_title(incident),
            description=issue_description(incident, approval_label=self.settings.linear.labels.approved,
                                          approve_command=self.settings.approval.comment_command, ownership_note=owner.note),  # fmt: skip
            priority=self._priority(incident),
            label_ids=add,
            assignee_id=owner.assignee_id,
            state_id=self._state_id(incident),
        )
        incident = self.store.update_incident(incident.model_copy(update={
            "linear_issue_id": issue.id, "linear_issue_identifier": issue.identifier,
            "assignee": owner.who if owner.assignee_id else None, "ownership_note": owner.note,
        }))  # fmt: skip
        self.audit.record(AuditEventType.LINEAR_ISSUE_CREATED, actor="agent", action="linear.create_issue", incident_id=incident.id,
                          output_summary=f"{issue.identifier} created", details={"url": issue.url, "labels": add, "priority": self._priority(incident)})  # fmt: skip
        self.audit.record(AuditEventType.OWNERSHIP_RESOLVED, actor="agent", action="resolve_owner", incident_id=incident.id,
                          output_summary=f"{owner.source}: {owner.who or 'unassigned'}", details={"notes": owner.notes})  # fmt: skip
        try:
            self.linear.add_comment(issue.id, diagnostics_comment(incident))
        except OncallError as exc:  # the issue exists; the summary is best effort
            self.audit.record(AuditEventType.INTEGRATION_FAILURE, actor="agent", action="linear.add_comment", incident_id=incident.id,
                              output_summary=str(exc), success=False)  # fmt: skip
        report.created.append(incident.id)  # type: ignore[arg-type]
        return self._mark_synced(incident)

    def _update(self, incident: Incident, report: LinearSyncReport, *, force: bool = False) -> Incident:
        from ..reporting import issue_description, issue_title, occurrence_comment, status_comment

        issue_id = incident.linear_issue_id
        assert issue_id
        if incident.linear_synced_occurrences and incident.occurrence_count > incident.linear_synced_occurrences:
            self.linear.add_comment(issue_id, occurrence_comment(incident))  # FR-075
        if force or incident.status.value != incident.linear_synced_status:
            add, remove = self._desired_labels(incident)
            self.linear.update_issue(
                issue_id,
                title=issue_title(incident),
                description=issue_description(incident, approval_label=self.settings.linear.labels.approved,
                                              approve_command=self.settings.approval.comment_command, ownership_note=incident.ownership_note),  # fmt: skip
                priority=self._priority(incident),
                state_id=self._state_id(incident),
                add_label_ids=add,
                remove_label_ids=remove,
            )
            self.linear.add_comment(issue_id, status_comment(incident))
        self.audit.record(AuditEventType.LINEAR_ISSUE_UPDATED, actor="agent", action="linear.update_issue", incident_id=incident.id,
                          output_summary=f"{incident.linear_issue_identifier}: {incident.status.value}, {incident.occurrence_count} occurrence(s)")  # fmt: skip
        report.updated.append(incident.id)  # type: ignore[arg-type]
        return self._mark_synced(incident)

    def _mark_synced(self, incident: Incident) -> Incident:
        # Re-read: the status may have changed while we talked to Linear; only mark what
        # we actually sent, so a newer change is pushed next cycle.
        current = self.store.get_incident(incident.id) or incident  # type: ignore[arg-type]
        return self.store.update_incident(current.model_copy(update={
            "linear_synced_status": incident.status.value, "linear_synced_occurrences": incident.occurrence_count,
        }))  # fmt: skip
