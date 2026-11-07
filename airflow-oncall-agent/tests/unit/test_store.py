"""SQLite store (SRS Part 7; FR-009/010/064/065/070/072; NFR-002/005; AT-007 persistence).

Covers the "SQLite transaction failure", "Agent restart", "Concurrent state updates",
"Duplicate retry requests" and "Approval received more than once" scenarios at the
persistence layer.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.exceptions import DuplicateExecutionError, StoreError
from oncall_agent.models import (
    ApprovalRecord,
    ApprovalSource,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    FailureEvent,
    FailureType,
    IncidentStatus,
)
from oncall_agent.store import SCHEMA_VERSION, Store

from ..conftest import make_incident

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


def retry_kwargs(incident_id: int, key: str = "auto:k1", trigger: str = "automatic", **limits) -> dict:
    data = dict(
        incident_id=incident_id, dag_id="sales_etl", dag_run_id="run1", task_id="extract_orders",
        map_index=-1, trigger_type=trigger, idempotency_key=key, original_try_number=1,
    )  # fmt: skip
    if trigger == "automatic":
        data.update(max_incident_retries=100, max_global_retries=100, global_window_start=T0 - timedelta(days=365))
        data.update(limits)
    return data


# --------------------------------------------------------------------------- initialisation and migrations


def test_open_creates_schema_with_wal_and_foreign_keys(store: Store):
    assert store.schema_version() == SCHEMA_VERSION
    tables = {r["name"] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"incidents", "polling_state", "processed_events", "retry_attempts", "approvals", "audit_events", "schema_version"} <= tables
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_required_indexes_exist(store: Store):  # §7.6
    indexes = {r["name"] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"ix_incidents_status", "ix_incidents_last_detected", "ix_audit_time", "ux_approvals_open", "ix_retry_trigger_time"} <= indexes
    # fingerprint uniqueness creates an automatic index
    assert any("incidents" in n and "autoindex" in n for n in indexes)


def test_reopening_does_not_reapply_migrations(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    with Store(path) as s:
        s.create_incident(make_incident())
    with Store(path) as s:
        assert s.schema_version() == SCHEMA_VERSION
        assert len(s.list_incidents()) == 1


def test_newer_schema_is_refused(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    with Store(path) as s:
        s.conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (999, 'x')")
    with pytest.raises(StoreError, match="newer"):
        Store(path).open()


def test_unwritable_location_raises_store_error(tmp_path: Path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError):
        Store(blocker / "sub" / "db.sqlite").open()


def test_using_a_closed_store_raises(tmp_path: Path):
    s = Store(tmp_path / "x.db")
    with pytest.raises(StoreError, match="not open"):
        s.get_watermark("p")


# --------------------------------------------------------------------------- watermarks (FR-009/010, NFR-002)


def test_watermark_persists_across_restart(tmp_path: Path):  # AT-007 (persistence part)
    path = tmp_path / "db.sqlite"
    with Store(path) as s:
        assert s.get_watermark("task_instances") is None
        assert s.set_watermark("task_instances", T0)
    with Store(path) as s:  # "restart"
        assert s.get_watermark("task_instances") == T0
        assert s.all_watermarks() == {"task_instances": T0}


def test_watermark_never_moves_backwards(store: Store):
    store.set_watermark("dag_runs", T0)
    assert store.set_watermark("dag_runs", T0 - timedelta(minutes=5)) is False
    assert store.get_watermark("dag_runs") == T0
    assert store.set_watermark("dag_runs", T0 + timedelta(seconds=1)) is True


def test_naive_watermark_is_treated_as_utc(store: Store):
    store.set_watermark("p", datetime(2026, 1, 1, 12, 0))
    assert store.get_watermark("p") == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- processed events (NFR-005)


def test_event_is_processed_only_once(store: Store):
    event = FailureEvent(failure_type=FailureType.TASK_FAILURE, dag_id="d", dag_run_id="r", task_id="t", try_number=1, state="failed")
    assert not store.is_event_processed(event.event_key)
    assert store.mark_event_processed(event, None) is True
    assert store.mark_event_processed(event, None) is False
    assert store.is_event_processed(event.event_key)


# --------------------------------------------------------------------------- incidents


def test_incident_round_trip_keeps_original_and_normalized_error(store: Store):  # FR-019/020
    created = store.create_incident(make_incident(evidence=["log line"], confidence=0.8, classification="retriable"))
    loaded = store.get_incident(created.id)
    assert loaded is not None
    assert loaded.original_error != loaded.normalized_error
    assert loaded.fingerprint_input == {"dag_id": "sales_etl", "task_id": "extract_orders", "normalized_error_line": "ReadTimeout"}
    assert loaded.evidence == ["log line"] and loaded.confidence == 0.8
    assert store.get_incident_by_fingerprint("a" * 64).id == created.id


def test_duplicate_fingerprint_is_rejected(store: Store):  # FR-016 at the schema level
    store.create_incident(make_incident())
    with pytest.raises(StoreError, match="Constraint"):
        store.create_incident(make_incident())


def test_update_and_list_by_status(store: Store):
    a = store.create_incident(make_incident("a"))
    store.create_incident(make_incident("b"))
    store.update_incident(a.model_copy(update={"status": IncidentStatus.RESOLVED, "linear_issue_id": "issue-1"}))
    assert [i.fingerprint[0] for i in store.list_incidents([IncidentStatus.RESOLVED])] == ["a"]
    assert len(store.list_incidents([IncidentStatus.OPEN])) == 1
    assert store.get_incident_by_linear_issue("issue-1").id == a.id


def test_update_of_missing_incident_fails(store: Store):
    with pytest.raises(StoreError):
        store.update_incident(make_incident().model_copy(update={"id": 999}))


def test_schema_check_constraints_reject_invalid_values(store: Store):
    store.create_incident(make_incident())
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE incidents SET confidence = 1.5")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE incidents SET proposed_action = 'delete_dag'")


# --------------------------------------------------------------------------- transactions (SQLite transaction failure)


def test_exception_inside_transaction_rolls_everything_back(store: Store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.create_incident(make_incident("a"))  # nested: joins the outer transaction
            store.set_watermark("p", T0)
            raise RuntimeError("boom")
    assert store.list_incidents() == []
    assert store.get_watermark("p") is None


def test_constraint_violation_rolls_back_whole_transaction(store: Store):
    with pytest.raises(StoreError):
        with store.transaction():
            store.create_incident(make_incident("a"))
            store.create_incident(make_incident("a"))  # duplicate fingerprint
    assert store.list_incidents() == []


def test_foreign_keys_are_enforced(store: Store):
    with pytest.raises(StoreError):
        store.start_retry_attempt(**retry_kwargs(incident_id=12345))


# --------------------------------------------------------------------------- retry attempts (FR-064/065)


def test_automatic_retry_updates_counters_and_numbers_attempts(store: Store):
    inc = store.create_incident(make_incident())
    r1 = store.start_retry_attempt(**retry_kwargs(inc.id, "auto:1"))
    r2 = store.start_retry_attempt(**retry_kwargs(inc.id, "approved:1", trigger="approved"))
    attempts = store.list_retry_attempts(inc.id)
    assert [a["attempt_number"] for a in attempts] == [1, 2]
    assert attempts[0]["status"] == "clearing" and attempts[0]["completed_at"] is None
    reloaded = store.get_incident(inc.id)
    assert (reloaded.retry_count, reloaded.cycle_retry_count) == (1, 1)  # only automatic counts
    store.finish_retry_attempt(r1, "verifying")
    store.finish_retry_attempt(r1, "succeeded", "task succeeded")
    done = store.get_retry_attempt(r1)
    assert done["status"] == "succeeded" and done["completed_at"] and done["result"] == "task succeeded"
    assert store.get_retry_attempt(r2)["trigger_type"] == "approved"


def test_duplicate_retry_is_refused_and_counts_once(store: Store):  # FR-065
    inc = store.create_incident(make_incident())
    store.start_retry_attempt(**retry_kwargs(inc.id, "auto:same"))
    with pytest.raises(DuplicateExecutionError):
        store.start_retry_attempt(**retry_kwargs(inc.id, "auto:same"))
    assert store.get_incident(inc.id).retry_count == 1
    assert len(store.list_retry_attempts(inc.id)) == 1


def test_concurrent_duplicate_retry_requests_execute_once(store: Store):  # concurrent state updates
    inc = store.create_incident(make_incident())
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            store.start_retry_attempt(**retry_kwargs(inc.id, "auto:race"))
            results.append("ok")
        except DuplicateExecutionError:
            results.append("dup")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("dup") == 7
    assert store.get_incident(inc.id).retry_count == 1


def test_concurrent_distinct_retries_get_unique_attempt_numbers(store: Store):
    inc = store.create_incident(make_incident())
    threads = [threading.Thread(target=store.start_retry_attempt, kwargs=retry_kwargs(inc.id, f"auto:{i}")) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(a["attempt_number"] for a in store.list_retry_attempts(inc.id)) == list(range(1, 11))
    assert store.get_incident(inc.id).retry_count == 10


def test_count_automatic_retries_in_window(store: Store):
    inc = store.create_incident(make_incident())
    store.start_retry_attempt(**retry_kwargs(inc.id, "auto:1"))
    store.start_retry_attempt(**retry_kwargs(inc.id, "approved:1", trigger="approved"))
    store.conn.execute("UPDATE retry_attempts SET started_at = ? WHERE idempotency_key = 'auto:1'", ((T0 - timedelta(hours=2)).isoformat(),))
    store.start_retry_attempt(**retry_kwargs(inc.id, "auto:2"))
    assert store.count_automatic_retries_since(datetime.now(timezone.utc) - timedelta(hours=1)) == 1
    assert store.count_automatic_retries_since(T0 - timedelta(days=1)) == 2


def test_invalid_retry_trigger_and_status_are_rejected(store: Store):
    inc = store.create_incident(make_incident())
    with pytest.raises(StoreError):
        store.start_retry_attempt(**retry_kwargs(inc.id, trigger="ai"))
    rid = store.start_retry_attempt(**retry_kwargs(inc.id))
    with pytest.raises(StoreError):
        store.finish_retry_attempt(rid, "done")


# --------------------------------------------------------------------------- approvals (FR-070/072)


def _approval(store: Store, incident_id: int) -> ApprovalRecord:
    return store.create_approval(ApprovalRecord(incident_id=incident_id, requested_action="rerun_dag", dag_id="sales_etl", dag_run_id="run1"))


def test_approval_lifecycle_records_source_approver_and_result(store: Store):  # FR-072
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    assert store.get_open_approval(inc.id).id == a.id
    assert store.record_approval(a.id, source=ApprovalSource.LINEAR_LABEL, approver="alice@example.com", approved_at=T0)
    assert store.claim_approval(a.id)
    store.complete_approval(a.id, succeeded=True, result="cleared 3 task instances")
    done = store.get_approval(a.id)
    assert done.status is ApprovalStatus.EXECUTED
    assert (done.approval_source, done.approver, done.approved_at) == (ApprovalSource.LINEAR_LABEL, "alice@example.com", T0)
    assert done.executed_at is not None and done.execution_result == "cleared 3 task instances"
    assert store.get_open_approval(inc.id) is None


def test_only_one_open_approval_per_incident(store: Store):
    inc = store.create_incident(make_incident())
    _approval(store, inc.id)
    with pytest.raises(StoreError):
        _approval(store, inc.id)


def test_approval_received_twice_changes_nothing_the_second_time(store: Store):
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    assert store.record_approval(a.id, source=ApprovalSource.LINEAR_LABEL, approver="alice", approved_at=T0) is True
    assert store.record_approval(a.id, source=ApprovalSource.LINEAR_COMMENT, approver="bob", approved_at=T0) is False
    assert store.get_approval(a.id).approver == "alice"


def test_approved_action_can_be_claimed_exactly_once_under_concurrency(store: Store):  # FR-070
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    store.record_approval(a.id, source=ApprovalSource.LINEAR_COMMENT, approver="alice", approved_at=T0)
    wins: list[bool] = []
    barrier = threading.Barrier(6)

    def worker() -> None:
        barrier.wait()
        wins.append(store.claim_approval(a.id))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1


def test_pending_approval_cannot_be_claimed(store: Store):
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    assert store.claim_approval(a.id) is False


def test_failed_execution_is_terminal(store: Store):
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    store.record_approval(a.id, source=ApprovalSource.LINEAR_COMMENT, approver="alice", approved_at=T0)
    store.claim_approval(a.id)
    store.complete_approval(a.id, succeeded=False, result="airflow 500")
    assert store.get_approval(a.id).status is ApprovalStatus.FAILED
    assert store.claim_approval(a.id) is False
    with pytest.raises(StoreError):
        store.complete_approval(a.id, succeeded=True, result="again")


def test_reject_only_pending_and_new_request_allowed_after(store: Store):
    inc = store.create_incident(make_incident())
    a = _approval(store, inc.id)
    assert store.reject_approval(a.id, "incident resolved") is True
    assert store.reject_approval(a.id, "again") is False
    b = _approval(store, inc.id)  # previous one is closed, so a new request is allowed
    assert [x.status for x in store.list_approvals(inc.id)] == [ApprovalStatus.REJECTED, ApprovalStatus.PENDING]
    assert store.list_approvals(statuses=[ApprovalStatus.PENDING])[0].id == b.id


# --------------------------------------------------------------------------- audit events


def test_audit_events_round_trip_in_order(store: Store):
    inc = store.create_incident(make_incident())
    store.insert_audit_event(AuditEvent(incident_id=inc.id, event_type=AuditEventType.INCIDENT_CREATED, actor="agent", action="create", timestamp=T0))
    store.insert_audit_event(AuditEvent(incident_id=inc.id, event_type=AuditEventType.POLICY_DECISION, actor="agent", action="evaluate", success=False, details={"reasons": ["x"]}, timestamp=T0 + timedelta(seconds=1)))
    store.insert_audit_event(AuditEvent(event_type=AuditEventType.POLL_COMPLETED, actor="agent", action="poll", timestamp=T0))
    events = store.list_audit_events(inc.id)
    assert [e.event_type for e in events] == [AuditEventType.INCIDENT_CREATED, AuditEventType.POLICY_DECISION]
    assert events[1].success is False and events[1].details == {"reasons": ["x"]}
    assert len(store.list_audit_events()) == 3


# --------------------------------------------------------------------------- atomic retry limits (FR-050, defence in depth)


def test_incident_retry_limit_is_enforced_inside_the_reservation(store: Store):
    from oncall_agent.exceptions import PolicyDeniedError

    inc = store.create_incident(make_incident())
    store.start_retry_attempt(**retry_kwargs(inc.id, "auto:1", max_incident_retries=1))
    with pytest.raises(PolicyDeniedError, match="incident retry limit"):
        store.start_retry_attempt(**retry_kwargs(inc.id, "auto:2", max_incident_retries=1))
    assert len(store.list_retry_attempts(inc.id)) == 1 and store.get_incident(inc.id).retry_count == 1


def test_global_retry_limit_is_enforced_inside_the_reservation(store: Store):
    from oncall_agent.exceptions import PolicyDeniedError

    a = store.create_incident(make_incident("a"))
    b = store.create_incident(make_incident("b"))
    store.start_retry_attempt(**retry_kwargs(a.id, "auto:a", max_global_retries=1, global_window_start=datetime.now(timezone.utc) - timedelta(hours=1)))
    with pytest.raises(PolicyDeniedError, match="global retry limit"):
        store.start_retry_attempt(**retry_kwargs(b.id, "auto:b", max_global_retries=1, global_window_start=datetime.now(timezone.utc) - timedelta(hours=1)))
    assert store.list_retry_attempts(b.id) == []


def test_automatic_reservation_without_limits_fails_closed(store: Store):
    from oncall_agent.exceptions import PolicyDeniedError

    inc = store.create_incident(make_incident())
    kwargs = retry_kwargs(inc.id)
    kwargs.update(max_incident_retries=None)
    with pytest.raises(PolicyDeniedError, match="fail closed"):
        store.start_retry_attempt(**kwargs)
