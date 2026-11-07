"""Incident deduplication (FR-016..018, NFR-005, NFR-034, AT-010; decision A3)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from oncall_agent.exceptions import StoreError
from oncall_agent.fingerprint import fingerprint_error
from oncall_agent.models import AnalysisSource, AuditEventType, Classification, FailureEvent, FailureType, IncidentStatus, ProposedAction
from oncall_agent.services.audit import AuditService
from oncall_agent.services.incident_manager import IncidentManager, RecordOutcome
from oncall_agent.store import Store

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


def event(run: str = "run1", try_number: int = 1, end: datetime = T0) -> FailureEvent:
    return FailureEvent(
        failure_type=FailureType.TASK_FAILURE, dag_id="sales_etl", dag_run_id=run, task_id="extract_orders",
        try_number=try_number, state="failed", end_date=end,
    )  # fmt: skip


def fp(text: str = "TimeoutError: read timed out at 0x7f00"):
    return fingerprint_error("sales_etl", "extract_orders", text, error_source="log")


@pytest.fixture
def manager(store: Store, audit: AuditService) -> IncidentManager:
    return IncidentManager(store, audit, clock=lambda: T0 + timedelta(hours=1))


def test_new_fingerprint_creates_an_open_incident(manager: IncidentManager, store: Store):
    result = manager.record_failure(event(), fp())
    assert result.outcome is RecordOutcome.CREATED and result.needs_investigation
    inc = store.get_incident(result.incident.id)
    assert inc.status is IncidentStatus.OPEN and inc.occurrence_count == 1
    assert inc.original_error == "TimeoutError: read timed out at 0x7f00"
    assert inc.normalized_error == "TimeoutError: read timed out at <addr>"
    assert inc.fingerprint_input["format"] == "dag_id|task_id|normalized_error_line"
    assert inc.first_detected_at == T0


def test_repeated_failure_reuses_the_incident(manager: IncidentManager, store: Store):  # AT-010 / FR-017
    first = manager.record_failure(event("run1"), fp("TimeoutError: read timed out at 0x7f00"))
    second = manager.record_failure(event("run2", end=T0 + timedelta(days=1)), fp("TimeoutError: read timed out at 0x7fff"))
    assert second.outcome is RecordOutcome.DUPLICATE and not second.needs_investigation
    assert second.incident.id == first.incident.id
    inc = store.get_incident(first.incident.id)
    assert inc.occurrence_count == 2 and inc.dag_run_id == "run2"
    assert inc.last_detected_at == T0 + timedelta(days=1) and inc.first_detected_at == T0
    assert len(store.list_incidents()) == 1


def test_same_observation_twice_changes_nothing(manager: IncidentManager, store: Store):  # NFR-005
    manager.record_failure(event(), fp())
    again = manager.record_failure(event(), fp())
    assert again.outcome is RecordOutcome.ALREADY_PROCESSED
    assert store.list_incidents()[0].occurrence_count == 1


def test_new_try_of_same_task_is_a_new_observation(manager: IncidentManager, store: Store):
    manager.record_failure(event(try_number=1), fp())
    assert manager.record_failure(event(try_number=2), fp()).outcome is RecordOutcome.DUPLICATE


def test_resolved_incident_is_reopened_and_cycle_counter_reset(manager: IncidentManager, store: Store):  # A3
    inc = manager.record_failure(event("run1"), fp()).incident
    store.update_incident(store.get_incident(inc.id).model_copy(update={
        "status": IncidentStatus.RESOLVED, "retry_count": 2, "cycle_retry_count": 2,
        "classification": Classification.RETRIABLE, "confidence": 0.9, "proposed_action": ProposedAction.RETRY_TASK,
        "analysis_source": AnalysisSource.AI,
        "root_cause": "old", "evidence": ["old"], "suspected_commit": "abc1234",
    }))  # fmt: skip
    result = manager.record_failure(event("run2", end=T0 + timedelta(days=2)), fp())
    assert result.outcome is RecordOutcome.REOPENED and result.needs_investigation
    reopened = store.get_incident(inc.id)
    assert reopened.status is IncidentStatus.OPEN
    assert reopened.cycle_retry_count == 0 and reopened.retry_count == 2  # lifetime total kept
    assert reopened.reopened_at == T0 + timedelta(hours=1)
    # The stale analysis must not drive decisions about the new failure.
    assert (reopened.classification, reopened.confidence, reopened.proposed_action, reopened.analysis_source) == (None, None, None, None)
    assert reopened.root_cause is None and reopened.evidence == [] and reopened.suspected_commit is None


def test_lifecycle_events_are_audited(manager: IncidentManager, audit: AuditService):  # NFR-026
    inc = manager.record_failure(event("run1"), fp()).incident
    manager.record_failure(event("run2"), fp())
    types = [e.event_type for e in audit.history(inc.id)]
    assert types == [AuditEventType.FAILURE_DETECTED, AuditEventType.INCIDENT_CREATED, AuditEventType.FAILURE_DETECTED, AuditEventType.INCIDENT_UPDATED]
    assert audit.history(inc.id)[1].details["fingerprint_input"]["dag_id"] == "sales_etl"


def test_failure_mid_transaction_leaves_no_partial_state(manager: IncidentManager, store: Store, monkeypatch):  # SQLite transaction failure
    def broken(*args, **kwargs):
        raise StoreError("disk I/O error")

    monkeypatch.setattr(store, "mark_event_processed", broken)
    with pytest.raises(StoreError):
        manager.record_failure(event(), fp())
    assert store.list_incidents() == []  # incident insert rolled back with it


def test_concurrent_recording_of_one_failure_creates_one_incident(manager: IncidentManager, store: Store):
    outcomes: list[RecordOutcome] = []
    barrier = threading.Barrier(5)

    def worker(run: str) -> None:
        barrier.wait()
        outcomes.append(manager.record_failure(event(run), fp()).outcome)

    threads = [threading.Thread(target=worker, args=(r,)) for r in ("run1", "run1", "run2", "run3", "run3")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(RecordOutcome.CREATED) == 1
    assert outcomes.count(RecordOutcome.ALREADY_PROCESSED) == 2
    assert len(store.list_incidents()) == 1 and store.list_incidents()[0].occurrence_count == 3


def test_folded_event_is_recorded_once(manager: IncidentManager, store: Store):
    run_event = FailureEvent(failure_type=FailureType.DAG_RUN_FAILURE, dag_id="d", dag_run_id="r", state="failed")
    assert manager.mark_event_without_incident(run_event, "tasks reported") is True
    assert manager.mark_event_without_incident(run_event, "tasks reported") is False
    assert store.list_incidents() == []
