"""Recovery service against the mock Airflow and a real store.

FR-050..065, FR-066/067/070 (execution side); AT-021..AT-024; Part 10 rules; scenarios
"Duplicate retry requests", "Concurrent state updates", "Retry still running after 30
minutes", "Retry fails again", "Global retry limit set to zero", "DAG marked as no-retry".
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.config import PolicySettings
from oncall_agent.exceptions import AirflowError
from oncall_agent.integrations.airflow import TaskInstance
from oncall_agent.integrations.mocks import MockAirflowClient
from oncall_agent.models import (
    AnalysisSource,
    ApprovalRecord,
    ApprovalSource,
    ApprovalStatus,
    AuditEventType,
    Classification,
    FailureType,
    Incident,
    IncidentStatus,
    ProposedAction,
)
from oncall_agent.policy import Decision, PolicyEngine
from oncall_agent.services.audit import AuditService
from oncall_agent.services.recovery import RecoveryService
from oncall_agent.store import Store

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def airflow() -> MockAirflowClient:
    return MockAirflowClient()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "db.sqlite").open()
    yield s
    s.close()


@pytest.fixture
def audit(store: Store, tmp_path: Path) -> AuditService:
    return AuditService(store, tmp_path / "audit.jsonl")


def make_recovery(airflow, store, audit, clock, **policy) -> RecoveryService:
    settings = PolicySettings(**{"max_auto_retries": 2, "global_max_retries_per_hour": 10, "no_retry_dags": ("finance_ledger_close",), **policy})
    return RecoveryService(airflow, store, audit, PolicyEngine(settings), settings, clock=clock)


@pytest.fixture
def recovery(airflow, store, audit, clock) -> RecoveryService:
    return make_recovery(airflow, store, audit, clock)


def failed_ti(airflow: MockAirflowClient, dag_id: str) -> TaskInstance:
    return next(t for t in airflow.tis.values() if t.dag_id == dag_id and t.state == "failed")


def eligible_incident(store: Store, airflow: MockAirflowClient, dag_id: str = "sales_etl", **overrides) -> Incident:
    ti = failed_ti(airflow, dag_id)
    data = dict(
        fingerprint=(dag_id[0] * 64), fingerprint_input={}, failure_type=FailureType.TASK_FAILURE, dag_id=dag_id, task_id=ti.task_id,
        dag_run_id=ti.dag_run_id, normalized_error="ReadTimeout", classification=Classification.RETRIABLE, confidence=0.85,
        proposed_action=ProposedAction.RETRY_TASK, analysis_source=AnalysisSource.AI, status=IncidentStatus.INVESTIGATING,
    )  # fmt: skip
    data.update(overrides)
    return store.create_incident(Incident(**data))


def states(airflow: MockAirflowClient, dag_id: str, run_id: str) -> dict[str, str | None]:
    return {t.task_id: t.state for t in airflow.list_task_instances(dag_id, run_id)}


def real_clears(airflow: MockAirflowClient) -> list:
    return [c for c in airflow.write_calls if c[0] == "clear_task_instances"]


# --------------------------------------------------------------------------- automatic retry (FR-056..059, AT-023)


def test_eligible_failure_is_cleared_with_its_upstream_failed_children(recovery, airflow, store, audit):  # FR-056/057
    inc = eligible_incident(store, airflow)
    result = recovery.auto_retry(inc)
    assert result.status == "cleared" and result.cleared == ["extract_orders", "load_orders", "transform_orders"]
    assert real_clears(airflow) == [("clear_task_instances", {"dag_id": "sales_etl", "dag_run_id": inc.dag_run_id, "task_ids": ["extract_orders", "load_orders", "transform_orders"]})]
    reloaded = store.get_incident(inc.id)
    assert reloaded.status is IncidentStatus.RETRYING and (reloaded.retry_count, reloaded.cycle_retry_count) == (1, 1)
    attempt = store.list_retry_attempts(inc.id)[0]
    assert attempt["status"] == "verifying" and attempt["original_try_number"] == 1 and attempt["trigger_type"] == "automatic"
    assert any(e.event_type is AuditEventType.RETRY_STARTED for e in audit.history(inc.id))  # NFR-029


def test_successful_tasks_are_never_cleared(recovery, airflow, store):  # AT-023 / FR-058
    inc = eligible_incident(store, airflow, "customer_sync")
    assert states(airflow, "customer_sync", inc.dag_run_id)["fetch_customers"] == "success"
    result = recovery.auto_retry(inc)
    assert "fetch_customers" not in result.cleared
    assert real_clears(airflow)[0][1]["task_ids"] == ["publish_customers", "transform_customers"]
    assert states(airflow, "customer_sync", inc.dag_run_id)["fetch_customers"] == "success"  # untouched


def _preview_returning(airflow, extra: TaskInstance):
    real = airflow.clear_task_instances

    def preview(dag_id, run_id, task_ids, *, dry_run, only_failed=True, include_downstream=False):
        result = real(dag_id, run_id, task_ids, dry_run=dry_run, only_failed=only_failed, include_downstream=include_downstream)
        return result + [extra] if dry_run else result

    return preview


@pytest.mark.parametrize(
    "extra,needle",
    [
        (lambda ti: ti.model_copy(update={"task_id": "load_orders", "state": "success"}), "only failed/upstream_failed"),  # FR-058
        (lambda ti: ti.model_copy(update={"dag_run_id": "other_run", "task_id": "x", "state": "upstream_failed"}), "outside the target run"),  # FR-059
        (lambda ti: ti.model_copy(update={"dag_id": "other_dag", "task_id": "x", "state": "upstream_failed"}), "outside the target run"),
        (lambda ti: ti.model_copy(update={"task_id": "independent_task", "state": "failed"}), "not an upstream_failed child"),  # FR-057
    ],
)
def test_unsafe_dry_run_blocks_the_retry(recovery, airflow, store, audit, monkeypatch, extra, needle):
    inc = eligible_incident(store, airflow)
    monkeypatch.setattr(airflow, "clear_task_instances", _preview_returning(airflow, extra(failed_ti(airflow, "sales_etl"))))
    result = recovery.auto_retry(inc)
    assert result.status == "blocked" and needle in result.detail
    assert airflow.write_calls == []  # nothing cleared
    assert store.list_retry_attempts(inc.id) == []  # and no retry counted
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED
    assert any(e.event_type is AuditEventType.RETRY_BLOCKED for e in audit.history(inc.id))


def test_empty_dry_run_blocks_the_retry(recovery, airflow, store, monkeypatch):
    inc = eligible_incident(store, airflow)
    monkeypatch.setattr(airflow, "clear_task_instances", lambda *a, **k: [])
    assert recovery.auto_retry(inc).status == "blocked" and airflow.write_calls == []


def test_task_no_longer_failed_is_not_retried(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    airflow.set_task_state("sales_etl", inc.dag_run_id, "extract_orders", "running")
    assert recovery.auto_retry(inc).status == "skipped" and airflow.write_calls == []


def test_task_already_successful_resolves_without_retry(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    airflow.set_task_state("sales_etl", inc.dag_run_id, "extract_orders", "success")
    assert recovery.auto_retry(inc).status == "resolved" and airflow.write_calls == []
    assert store.get_incident(inc.id).status is IncidentStatus.RESOLVED


def test_task_state_unreadable_fails_closed(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    airflow.fail_next("get_task_instance", AirflowError("timeout", retriable=True))
    assert recovery.auto_retry(inc).status == "error" and airflow.write_calls == []
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED


# --------------------------------------------------------------------------- policy cannot be bypassed (FR-049, Part 10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"classification": Classification.UNCERTAIN},
        {"confidence": 0.5},
        {"analysis_source": AnalysisSource.FALLBACK, "confidence": 0.5},  # AT-017
        {"proposed_action": ProposedAction.RERUN_DAG},
        {"cycle_retry_count": 2},  # AT-021
    ],
)
def test_direct_call_with_ineligible_incident_is_refused(recovery, airflow, store, overrides):
    inc = eligible_incident(store, airflow, **overrides)
    assert recovery.auto_retry(inc).status == "denied"
    assert airflow.write_calls == [] and store.list_retry_attempts(inc.id) == []


def test_no_retry_dag_is_never_retried(recovery, airflow, store):  # "DAG marked as no-retry"
    inc = eligible_incident(store, airflow, "finance_ledger_close")
    assert recovery.auto_retry(inc).status == "denied" and airflow.write_calls == []


@pytest.mark.parametrize("policy", [{"max_auto_retries": 0}, {"global_max_retries_per_hour": 0}])
def test_zero_limits_disable_retries(airflow, store, audit, clock, policy):  # FR-051 / "Global retry limit set to zero"
    recovery = make_recovery(airflow, store, audit, clock, **policy)
    inc = eligible_incident(store, airflow)
    assert recovery.auto_retry(inc).status == "denied" and airflow.write_calls == []


def test_global_hourly_limit_is_enforced(airflow, store, audit, clock):  # AT-022
    recovery = make_recovery(airflow, store, audit, clock, global_max_retries_per_hour=1)
    first = eligible_incident(store, airflow, "sales_etl")
    second = eligible_incident(store, airflow, "customer_sync")
    assert recovery.auto_retry(first).status == "cleared"
    assert recovery.auto_retry(second).status == "denied"
    assert len(real_clears(airflow)) == 1
    clock.now = NOW + timedelta(hours=1, minutes=1)  # window has passed
    third = eligible_incident(store, airflow, "ml_feature_build")
    assert recovery.auto_retry(third).status == "cleared"


def test_global_limit_holds_under_concurrency(airflow, store, audit, clock):  # concurrent state updates
    recovery = make_recovery(airflow, store, audit, clock, global_max_retries_per_hour=1)
    incidents = [eligible_incident(store, airflow, d) for d in ("sales_etl", "customer_sync", "ml_feature_build")]
    barrier = threading.Barrier(3)
    results: list[str] = []

    def worker(inc):
        barrier.wait()
        results.append(recovery.auto_retry(inc).status)

    threads = [threading.Thread(target=worker, args=(i,)) for i in incidents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("cleared") == 1 and len(real_clears(airflow)) == 1


# --------------------------------------------------------------------------- duplicates (FR-065)


def test_duplicate_retry_request_is_refused_without_escalating(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    assert recovery.auto_retry(inc).status == "cleared"
    again = recovery.auto_retry(inc)
    assert again.status == "duplicate" and len(real_clears(airflow)) == 1
    assert store.get_incident(inc.id).status is IncidentStatus.RETRYING  # not escalated by the duplicate


def test_concurrent_retry_requests_clear_once(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    barrier = threading.Barrier(5)
    results: list[str] = []

    def worker():
        barrier.wait()
        results.append(recovery.auto_retry(inc).status)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("cleared") == 1 and len(real_clears(airflow)) == 1
    assert store.get_incident(inc.id).retry_count == 1


def test_same_failed_try_cannot_be_retried_twice_even_after_status_reset(recovery, airflow, store):
    inc = eligible_incident(store, airflow)
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "running"
    recovery.auto_retry(inc)
    # Someone resets the incident, but Airflow still shows the original failed try:
    airflow.set_task_state("sales_etl", inc.dag_run_id, "extract_orders", "failed")
    airflow.tis[("sales_etl", inc.dag_run_id, "extract_orders", -1)] = airflow.tis[("sales_etl", inc.dag_run_id, "extract_orders", -1)].model_copy(update={"try_number": 1})
    store.update_incident(store.get_incident(inc.id).model_copy(update={"status": IncidentStatus.INVESTIGATING}))
    assert recovery.auto_retry(inc).status == "duplicate"  # idempotency key auto:...:1
    assert len(real_clears(airflow)) == 1


def test_real_clear_timeout_is_not_repeated_and_is_verified(recovery, airflow, store, monkeypatch):
    inc = eligible_incident(store, airflow)
    real = airflow.clear_task_instances
    calls = {"real": 0}

    def flaky(dag_id, run_id, task_ids, *, dry_run, **kw):
        if not dry_run:
            calls["real"] += 1
            raise AirflowError("timed out", retriable=True)
        return real(dag_id, run_id, task_ids, dry_run=True, **kw)

    monkeypatch.setattr(airflow, "clear_task_instances", flaky)
    result = recovery.auto_retry(inc)
    assert result.status == "cleared" and "outcome unknown" in result.detail and calls["real"] == 1
    assert store.list_retry_attempts(inc.id)[0]["status"] == "verifying"


def test_rejected_clear_escalates(recovery, airflow, store, monkeypatch):
    inc = eligible_incident(store, airflow)
    real = airflow.clear_task_instances
    monkeypatch.setattr(airflow, "clear_task_instances", lambda d, r, t, *, dry_run, **kw: real(d, r, t, dry_run=True, **kw) if dry_run else (_ for _ in ()).throw(AirflowError("403 forbidden", status_code=403)))
    assert recovery.auto_retry(inc).status == "error"
    assert store.list_retry_attempts(inc.id)[0]["status"] == "error"
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED


# --------------------------------------------------------------------------- verification (FR-060..064)


def test_successful_retry_resolves_the_incident(recovery, airflow, store, audit):  # FR-061
    inc = eligible_incident(store, airflow)
    recovery.auto_retry(inc)
    [v] = recovery.verify_pending()
    assert v.outcome == "succeeded"
    assert store.get_incident(inc.id).status is IncidentStatus.RESOLVED
    assert store.list_retry_attempts(inc.id)[0]["status"] == "succeeded"
    types = [e.event_type for e in audit.history(inc.id)]
    assert AuditEventType.RETRY_SUCCEEDED in types and AuditEventType.INCIDENT_RESOLVED in types


def test_retry_that_fails_again_escalates(recovery, airflow, store):  # FR-063 / "Retry fails again"
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "failed"
    inc = eligible_incident(store, airflow)
    recovery.auto_retry(inc)
    [v] = recovery.verify_pending()
    assert v.outcome == "failed" and "try 2" in v.detail
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED
    assert store.list_retry_attempts(inc.id)[0]["status"] == "failed"


def test_retry_still_running_after_30_minutes_escalates(recovery, airflow, store, clock):  # FR-062
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "running"
    inc = eligible_incident(store, airflow)
    recovery.auto_retry(inc)
    clock.now = datetime.now(timezone.utc) + timedelta(minutes=29)
    assert recovery.verify_pending()[0].outcome == "pending"
    assert store.get_incident(inc.id).status is IncidentStatus.RETRYING
    clock.now = datetime.now(timezone.utc) + timedelta(minutes=31)
    [v] = recovery.verify_pending()
    assert v.outcome == "timed_out"
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED
    assert store.list_retry_attempts(inc.id)[0]["status"] == "timed_out"
    assert recovery.verify_pending() == []  # no longer verifying


def test_unreadable_state_keeps_verifying_until_timeout(recovery, airflow, store, clock):
    inc = eligible_incident(store, airflow)
    recovery.auto_retry(inc)
    airflow.unavailable = True
    assert recovery.verify_pending()[0].outcome == "pending"
    clock.now = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert recovery.verify_pending()[0].outcome == "timed_out"


def test_clear_that_never_took_effect_is_not_reported_as_failed_again(recovery, airflow, store, clock):
    inc = eligible_incident(store, airflow)
    recovery.auto_retry(inc)
    airflow.tis[("sales_etl", inc.dag_run_id, "extract_orders", -1)] = airflow.tis[("sales_etl", inc.dag_run_id, "extract_orders", -1)].model_copy(update={"state": "failed", "try_number": 1})
    assert recovery.verify_pending()[0].outcome == "pending"


def test_verification_survives_a_restart(airflow, tmp_path, clock):  # NFR-002
    path = tmp_path / "restart.db"
    with Store(path) as s:
        r = make_recovery(airflow, s, AuditService(s, None), clock)
        r.auto_retry(eligible_incident(s, airflow))
    with Store(path) as s:
        r = make_recovery(airflow, s, AuditService(s, None), clock)
        assert [v.outcome for v in r.verify_pending()] == ["succeeded"]


# --------------------------------------------------------------------------- decisions (FR-053/055)


def test_decide_and_act_routes_each_decision(recovery, airflow, store, audit):
    retry = eligible_incident(store, airflow, "sales_etl")
    approval = eligible_incident(store, airflow, "customer_sync", proposed_action=ProposedAction.RERUN_DAG, classification=Classification.UNCERTAIN)
    escalate = eligible_incident(store, airflow, "ml_feature_build", classification=Classification.UNCERTAIN, proposed_action=ProposedAction.MANUAL_INVESTIGATION)
    nothing = eligible_incident(store, airflow, "finance_ledger_close", proposed_action=ProposedAction.NO_ACTION)
    outcomes = {inc.dag_id: recovery.decide_and_act(inc) for inc in (retry, approval, escalate, nothing)}
    assert {d: (dec.decision, res.status) for d, (dec, res) in outcomes.items()} == {
        "sales_etl": (Decision.AUTO_RETRY, "cleared"),
        "customer_sync": (Decision.REQUEST_APPROVAL, "requested"),
        "ml_feature_build": (Decision.ESCALATE, "escalated"),
        "finance_ledger_close": (Decision.NO_ACTION, "escalated"),  # never silently closed
    }
    assert store.get_incident(approval.id).status is IncidentStatus.AWAITING_APPROVAL
    assert store.get_open_approval(approval.id).requested_action is ProposedAction.RERUN_DAG
    decisions = [e for e in store.list_audit_events() if e.event_type is AuditEventType.POLICY_DECISION]
    assert len(decisions) == 4 and all(len(e.details["checks"]) == 10 for e in decisions)  # FR-053
    assert len(real_clears(airflow)) == 1  # only the eligible one


def test_restricted_action_is_never_executed_without_approval(recovery, airflow, store):  # FR-073
    inc = eligible_incident(store, airflow, "customer_sync", proposed_action=ProposedAction.MARK_SUCCESS, confidence=0.99)
    recovery.decide_and_act(inc)
    recovery.decide_and_act(inc)  # asked twice: still one request, nothing executed
    assert airflow.write_calls == [] and len(store.list_approvals(inc.id)) == 1


# --------------------------------------------------------------------------- approved actions (FR-066/067/070, AT-024 execution side)


def approved(store: Store, inc: Incident, action: ProposedAction) -> ApprovalRecord:
    record = store.create_approval(ApprovalRecord(incident_id=inc.id, requested_action=action, dag_id=inc.dag_id, dag_run_id=inc.dag_run_id, task_id=inc.task_id))
    store.record_approval(record.id, source=ApprovalSource.LINEAR_LABEL, approver="alice@example.com", approved_at=NOW)
    return record


def test_approved_rerun_clears_only_failed_tasks_of_that_run_once(recovery, airflow, store, audit):  # A1 / FR-070
    inc = eligible_incident(store, airflow, "customer_sync", status=IncidentStatus.AWAITING_APPROVAL)
    record = approved(store, inc, ProposedAction.RERUN_DAG)
    result = recovery.execute_approved(record.id)
    assert result.status == "executed" and result.cleared == ["publish_customers", "transform_customers"]
    assert states(airflow, "customer_sync", inc.dag_run_id)["fetch_customers"] == "success"
    assert store.get_approval(record.id).status is ApprovalStatus.EXECUTED
    assert recovery.execute_approved(record.id).status == "denied"  # already executed
    assert len(real_clears(airflow)) == 1
    executed = [e for e in audit.history(inc.id) if e.event_type is AuditEventType.ACTION_EXECUTED][0]
    assert executed.details["approver"] == "alice@example.com"
    [v] = recovery.verify_pending()
    assert v.outcome == "succeeded" and store.get_incident(inc.id).status is IncidentStatus.RESOLVED


def test_concurrent_execution_of_one_approval_runs_once(recovery, airflow, store):
    inc = eligible_incident(store, airflow, "customer_sync", status=IncidentStatus.AWAITING_APPROVAL)
    record = approved(store, inc, ProposedAction.RERUN_DAG)
    barrier = threading.Barrier(4)
    results: list[str] = []

    def worker():
        barrier.wait()
        results.append(recovery.execute_approved(record.id).status)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("executed") == 1 and len(real_clears(airflow)) == 1


def test_pending_approval_cannot_be_executed(recovery, airflow, store):
    inc = eligible_incident(store, airflow, "customer_sync")
    record = store.create_approval(ApprovalRecord(incident_id=inc.id, requested_action=ProposedAction.RERUN_DAG, dag_id=inc.dag_id, dag_run_id=inc.dag_run_id))
    assert recovery.execute_approved(record.id).status == "denied" and airflow.write_calls == []


def test_approved_rerun_with_nothing_to_clear_fails_safely(recovery, airflow, store):
    run = next(r for (d, r), v in airflow.runs.items() if d == "reporting_daily" and v.state == "failed")
    inc = store.create_incident(Incident(fingerprint="r" * 64, fingerprint_input={}, failure_type=FailureType.DAG_RUN_FAILURE, dag_id="reporting_daily",
                                         dag_run_id=run, normalized_error="x", status=IncidentStatus.AWAITING_APPROVAL))  # fmt: skip
    record = approved(store, inc, ProposedAction.RERUN_DAG)
    result = recovery.execute_approved(record.id)
    assert result.status == "failed" and "no failed or upstream_failed" in result.detail
    assert airflow.write_calls == [] and store.get_approval(record.id).status is ApprovalStatus.FAILED
    assert store.get_incident(inc.id).status is IncidentStatus.ESCALATED


def test_approved_action_on_no_retry_dag_is_refused(recovery, airflow, store):
    inc = eligible_incident(store, airflow, "finance_ledger_close", status=IncidentStatus.AWAITING_APPROVAL)
    record = approved(store, inc, ProposedAction.RERUN_DAG)
    assert recovery.execute_approved(record.id).status == "failed" and airflow.write_calls == []


def test_approved_mark_success_touches_only_the_target(recovery, airflow, store):  # FR-067
    inc = eligible_incident(store, airflow, "ml_feature_build", status=IncidentStatus.AWAITING_APPROVAL)
    record = approved(store, inc, ProposedAction.MARK_SUCCESS)
    assert recovery.execute_approved(record.id).status == "executed"
    assert states(airflow, "ml_feature_build", inc.dag_run_id) == {"build_features": "success", "train_model": "upstream_failed"}
    assert store.get_incident(inc.id).status is IncidentStatus.RESOLVED


def test_mark_success_preview_with_extra_tasks_is_refused(recovery, airflow, store, monkeypatch):
    inc = eligible_incident(store, airflow, "ml_feature_build", status=IncidentStatus.AWAITING_APPROVAL)
    record = approved(store, inc, ProposedAction.MARK_SUCCESS)
    real = airflow.set_task_instance_state

    def wider(dag_id, run_id, task_id, state, *, map_index=-1, dry_run):
        result = real(dag_id, run_id, task_id, state, map_index=map_index, dry_run=dry_run)
        return result + [result[0].model_copy(update={"task_id": "train_model"})] if dry_run else result

    monkeypatch.setattr(airflow, "set_task_instance_state", wider)
    assert recovery.execute_approved(record.id).status == "failed" and airflow.write_calls == []


def test_resolving_rejects_a_pending_approval(recovery, airflow, store):
    inc = eligible_incident(store, airflow, "customer_sync", proposed_action=ProposedAction.RERUN_DAG)
    recovery.decide_and_act(inc)
    recovery.resolve(store.get_incident(inc.id), "fixed by a human")
    assert store.list_approvals(inc.id)[0].status is ApprovalStatus.REJECTED


@pytest.mark.parametrize("policy,second_dag", [({"global_max_retries_per_hour": 1}, "customer_sync"), ({"max_auto_retries": 1}, "sales_etl")])
def test_stale_policy_decision_is_still_stopped_by_the_atomic_limits(airflow, store, audit, clock, monkeypatch, policy, second_dag):
    """Even if the policy evaluation were stale (always 'allowed'), the reservation
    re-checks both limits in its own transaction (FR-050 defence in depth)."""
    recovery = make_recovery(airflow, store, audit, clock, **policy)
    stale = recovery.policy.evaluate(eligible_incident(store, airflow, "ml_feature_build"), 0)
    assert stale.auto_retry_allowed
    monkeypatch.setattr(recovery.policy, "evaluate", lambda incident, count: stale)
    first = eligible_incident(store, airflow, "sales_etl")
    assert recovery.auto_retry(first).status == "cleared"
    if second_dag == "sales_etl":  # same incident, a new failed try (retry failed again)
        airflow.set_task_state("sales_etl", first.dag_run_id, "extract_orders", "failed")
        store.update_incident(store.get_incident(first.id).model_copy(update={"status": IncidentStatus.INVESTIGATING}))
        second = first
    else:
        second = eligible_incident(store, airflow, second_dag)
    result = recovery.auto_retry(second)
    assert result.status == "denied" and "at reservation" in result.detail
    assert len(real_clears(airflow)) == 1
