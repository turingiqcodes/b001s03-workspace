"""Monitoring cycle against the mock Airflow and a real SQLite store.

AT-004..AT-008, AT-010; FR-004..012, FR-016..018; NFR-001/003/004/005/016;
scenarios "Airflow unavailable", "Malformed API responses", "Repeated polling",
"Agent restart", "SQLite transaction failure".
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.agent import Agent
from oncall_agent.config import Settings, load_settings
from oncall_agent.exceptions import AirflowError, MalformedResponseError, StoreError
from oncall_agent.factory import Integrations
from oncall_agent.fingerprint import NO_LOG
from oncall_agent.integrations.airflow import DagRun, TaskInstance
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.models import AuditEventType, FailureType, IncidentStatus
from oncall_agent.services.audit import AuditService
from oncall_agent.services.monitoring import IMPORT_POLLER, MAX_LOG_DEFERRALS, POLLERS, TASK_POLLER
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc).replace(microsecond=0)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def settings() -> Settings:
    # These tests are about detection; automatic retries are disabled so Airflow state
    # stays fixed. Recovery is covered by tests/integration/test_recovery.py.
    return load_settings({"APP_ENV": "development", "ONCALL_MAX_AUTO_RETRIES": "0"}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)


@pytest.fixture
def airflow() -> MockAirflowClient:
    return MockAirflowClient()


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "agent.db"


def make_agent(settings: Settings, airflow: MockAirflowClient, db_path: Path, clock: Clock) -> Agent:
    store = Store(db_path).open()
    audit = AuditService(store, db_path.parent / "audit.jsonl")
    integrations = Integrations(airflow, MockGitHubClient(), MockLinearClient(), mode="mock")
    return Agent(settings, store, audit, integrations, clock=clock)


@pytest.fixture
def agent(settings, airflow, db_path, clock) -> Agent:
    a = make_agent(settings, airflow, db_path, clock)
    yield a
    a.store.close()


def by_dag(agent: Agent) -> dict[str, object]:
    return {i.dag_id: i for i in agent.store.list_incidents()}


def add_failed_run(airflow: MockAirflowClient, dag_id: str, task_id: str, end: datetime, log: str) -> str:
    run_id = f"manual__{end.isoformat()}"
    airflow.runs[(dag_id, run_id)] = DagRun(dag_id=dag_id, dag_run_id=run_id, state="failed", start_date=end - timedelta(minutes=5), end_date=end)
    airflow.tis[(dag_id, run_id, task_id, -1)] = TaskInstance(dag_id=dag_id, dag_run_id=run_id, task_id=task_id, state="failed", try_number=1, start_date=end - timedelta(minutes=5), end_date=end)
    airflow.logs[(dag_id, run_id, task_id, 1)] = log
    return run_id


# --------------------------------------------------------------------------- detection (AT-004/005/006, FR-008)


def test_detects_failed_tasks(agent: Agent):  # AT-004 / FR-006
    report = agent.run_once()
    incidents = by_dag(agent)
    task_incidents = {d for d, i in incidents.items() if i.failure_type is FailureType.TASK_FAILURE}
    assert task_incidents == {"sales_etl", "customer_sync", "ml_feature_build", "finance_ledger_close"}
    sales = incidents["sales_etl"]
    assert sales.task_id == "extract_orders" and sales.dag_run_id.startswith("scheduled__")
    assert sales.normalized_error.startswith("requests.exceptions.ReadTimeout")
    assert report.outcomes["created"] == 6 and report.ok


def test_detects_failed_dag_run_without_failed_task(agent: Agent):  # AT-005 / FR-005
    report = agent.run_once()
    run_incident = by_dag(agent)["reporting_daily"]
    assert run_incident.failure_type is FailureType.DAG_RUN_FAILURE and run_incident.task_id is None
    assert run_incident.fingerprint_input["task_id"] == "__dag_run__"
    # The 4 failed runs that do have failed tasks are folded, not duplicated.
    assert report.folded == 4
    assert len(agent.store.list_incidents()) == 6


def test_detects_import_errors(agent: Agent):  # AT-006 / FR-007
    agent.run_once()
    imp = by_dag(agent)["/opt/airflow/dags/broken_dag.py"]
    assert imp.failure_type is FailureType.IMPORT_ERROR
    assert imp.normalized_error == "SyntaxError: invalid syntax"
    assert imp.fingerprint_input["task_id"] == "__import_error__"


def test_detected_events_carry_ids_dates_and_states(agent: Agent):  # FR-008
    batch = agent.monitoring.poll()
    event = next(e for _, e in batch.events if e.dag_id == "sales_etl")
    assert (event.dag_id, event.task_id, event.state, event.try_number) == ("sales_etl", "extract_orders", "failed", 1)
    assert event.dag_run_id and event.logical_date and event.start_date and event.end_date


def test_new_and_reopened_incidents_are_queued_for_investigation(agent: Agent):
    report = agent.run_once()
    assert len(report.to_investigate) == 6
    assert agent.run_once().to_investigate == []


# --------------------------------------------------------------------------- watermarks (AT-007/008, FR-009..011)


def test_watermarks_are_persisted_and_resumed_after_restart(settings, airflow, db_path, clock):  # AT-007 / FR-010
    first = make_agent(settings, airflow, db_path, clock)
    first.run_once()
    saved = first.store.all_watermarks()
    assert set(saved) == set(POLLERS)
    first.store.close()

    clock.now = NOW + timedelta(minutes=10)
    restarted = make_agent(settings, airflow, db_path, clock)  # same database = restart
    airflow.calls.clear()
    restarted.run_once()
    overlap = timedelta(seconds=settings.polling.overlap_seconds)
    sent = [args["end_date_gte"] for name, args in airflow.calls if name == "list_task_instances" and args.get("dag_id") == "~"]
    runs_sent = [args["end_date_gte"] for name, args in airflow.calls if name == "list_dag_runs"]
    # The restarted agent continued from the stored watermarks minus the overlap window,
    # not from "now - initial lookback".
    assert sent == [saved[TASK_POLLER] - overlap]
    assert runs_sent == [saved["dag_runs"] - overlap]
    assert len(restarted.store.list_incidents()) == 6  # nothing re-created
    restarted.store.close()


def test_failed_poll_does_not_advance_watermark(agent: Agent, airflow: MockAirflowClient, clock: Clock):  # AT-008 / FR-011
    agent.run_once()
    before = agent.store.all_watermarks()
    clock.now = NOW + timedelta(minutes=30)
    airflow.unavailable = True
    report = agent.run_once()
    assert set(report.failed_pollers) == set(POLLERS)
    assert agent.store.all_watermarks() == before
    failures = [e for e in agent.audit.store.list_audit_events() if e.event_type is AuditEventType.POLL_FAILED]
    assert len(failures) == 3 and all(e.details["watermark_advanced"] is False for e in failures)


def test_failed_poll_on_first_run_creates_no_watermark(agent: Agent, airflow: MockAirflowClient):
    airflow.unavailable = True
    agent.run_once()
    assert agent.store.all_watermarks() == {}


def test_one_failing_poller_does_not_block_the_others(agent: Agent, airflow: MockAirflowClient):  # NFR-004
    airflow.fail_next("list_import_errors", AirflowError("timeout", retriable=True))
    report = agent.run_once()
    assert report.failed_pollers == [IMPORT_POLLER]
    marks = agent.store.all_watermarks()
    assert IMPORT_POLLER not in marks and TASK_POLLER in marks
    assert len(agent.store.list_incidents()) == 5
    agent.run_once()  # recovers next cycle
    assert len(agent.store.list_incidents()) == 6


def test_malformed_airflow_response_fails_the_poll_safely(agent: Agent, airflow: MockAirflowClient):
    airflow.fail_next("list_task_instances", MalformedResponseError("airflow", "missing 'task_instances' list"))
    report = agent.run_once()
    assert TASK_POLLER in report.failed_pollers
    assert TASK_POLLER not in agent.store.all_watermarks()


def test_airflow_recovers_after_outage_without_losing_failures(agent: Agent, airflow: MockAirflowClient, clock: Clock):  # NFR-001
    airflow.unavailable = True
    agent.run_once()
    clock.now = NOW + timedelta(minutes=2)
    airflow.unavailable = False
    agent.run_once()
    assert len(agent.store.list_incidents()) == 6


def test_unexpected_poller_bug_is_contained(agent: Agent, airflow: MockAirflowClient):
    airflow.fail_next("list_dag_runs", RuntimeError("bug"))
    report = agent.run_once()
    assert "dag_runs" in report.failed_pollers and len(agent.store.list_incidents()) == 5


# --------------------------------------------------------------------------- repeated polling and duplicates (NFR-005/016, AT-010)


def test_repeated_polling_of_the_same_window_creates_no_duplicates(agent: Agent, airflow: MockAirflowClient, settings: Settings):
    agent.run_once()
    # Force the next poll to re-read everything (as an overlap window would).
    for name in POLLERS:
        agent.store.conn.execute("DELETE FROM polling_state WHERE poller_name = ?", (name,))
    airflow.calls.clear()
    report = agent.run_once()
    # 5 task/import events are recognised as already processed; the already-handled
    # failed DAG run is skipped inside the poller before it is even reported.
    assert report.outcomes == {"already_processed": 5} and report.folded == 0
    assert len(agent.store.list_incidents()) == 6
    # NFR-016: no logs downloaded and no task lookups for already-handled observations.
    assert airflow.call_count("get_task_log") == 0
    assert [a for n, a in airflow.calls if n == "list_task_instances" and a.get("dag_id") != "~"] == []


def test_same_failure_in_a_later_run_updates_the_existing_incident(agent: Agent, airflow: MockAirflowClient, clock: Clock):  # AT-010
    agent.run_once()
    original = by_dag(agent)["customer_sync"]
    later = NOW + timedelta(hours=1)
    run_id = add_failed_run(airflow, "customer_sync", "transform_customers", later, "Traceback (most recent call last):\nKeyError: 'customer_tier'")
    clock.now = later + timedelta(minutes=1)
    report = agent.run_once()
    assert report.outcomes["duplicate"] == 1 and report.outcomes.get("created", 0) == 0
    updated = agent.store.get_incident(original.id)
    assert updated.occurrence_count == 2 and updated.dag_run_id == run_id
    assert len(agent.store.list_incidents()) == 6


def test_resolved_incident_is_reopened_by_a_new_failure(agent: Agent, airflow: MockAirflowClient, clock: Clock):
    agent.run_once()
    inc = by_dag(agent)["customer_sync"]
    agent.store.update_incident(inc.model_copy(update={"status": IncidentStatus.RESOLVED}))
    later = NOW + timedelta(days=1)
    add_failed_run(airflow, "customer_sync", "transform_customers", later, "KeyError: 'customer_tier'")
    clock.now = later + timedelta(minutes=1)
    report = agent.run_once()
    assert report.outcomes["reopened"] == 1
    reopened = agent.store.get_incident(inc.id)
    assert reopened.reopened_at == clock.now
    # Reopened → investigated, analysed and decided again in the same cycle.
    assert [i.id for i in report.to_investigate] == [inc.id] and inc.id in report.contexts and inc.id in report.analyses
    assert reopened.status is IncidentStatus.ESCALATED  # non-retriable code error


# --------------------------------------------------------------------------- task log availability (FR-028)


def test_temporarily_unavailable_log_defers_the_event_and_holds_the_watermark(agent: Agent, airflow: MockAirflowClient, clock: Clock):
    airflow.fail_next("get_task_log", AirflowError("503", retriable=True))
    report = agent.run_once()
    assert report.deferred == 1 and len(agent.store.list_incidents()) == 5
    held = agent.store.get_watermark(TASK_POLLER)
    deferred_end = min(t.end_date for t in airflow.tis.values() if t.state == "failed")
    assert held < deferred_end + timedelta(microseconds=1)
    clock.now = NOW + timedelta(minutes=1)
    report = agent.run_once()
    assert report.outcomes["created"] == 1 and len(agent.store.list_incidents()) == 6


def test_log_that_stays_unavailable_is_eventually_processed_without_it(agent: Agent, airflow: MockAirflowClient, clock: Clock):
    for _ in range(MAX_LOG_DEFERRALS):
        airflow.fail_next("get_task_log", AirflowError("503", retriable=True))
    run = next(r for (d, r) in airflow.runs if d == "finance_ledger_close" and airflow.runs[(d, r)].state == "failed")
    airflow.logs.pop(("finance_ledger_close", run, "close_ledger", 1))  # make it this task that keeps failing
    for _ in range(MAX_LOG_DEFERRALS):
        agent.run_once()
        clock.now += timedelta(minutes=1)
    inc = by_dag(agent)["finance_ledger_close"]
    assert inc.normalized_error == NO_LOG and inc.fingerprint_input["error_source"] == "none"
    assert any(e.event_type is AuditEventType.INTEGRATION_FAILURE for e in agent.store.list_audit_events())


def test_missing_log_is_processed_immediately_without_it(agent: Agent, airflow: MockAirflowClient):
    run = next(r for (d, r) in airflow.runs if d == "ml_feature_build" and airflow.runs[(d, r)].state == "failed")
    airflow.logs.pop(("ml_feature_build", run, "build_features", 1))  # 404, not transient
    report = agent.run_once()
    assert report.deferred == 0 and by_dag(agent)["ml_feature_build"].normalized_error == NO_LOG


# --------------------------------------------------------------------------- processing failures (NFR-003/004)


def test_store_failure_while_recording_holds_that_pollers_watermark(agent: Agent, monkeypatch):  # SQLite transaction failure
    original = agent.incidents.record_failure
    calls = {"n": 0}

    def flaky(event, fp):
        calls["n"] += 1
        if event.failure_type is FailureType.IMPORT_ERROR:
            raise StoreError("database is locked")
        return original(event, fp)

    monkeypatch.setattr(agent.incidents, "record_failure", flaky)
    report = agent.run_once()
    assert report.errors and IMPORT_POLLER not in agent.store.all_watermarks()
    assert TASK_POLLER in agent.store.all_watermarks()
    monkeypatch.setattr(agent.incidents, "record_failure", original)
    agent.run_once()
    assert len(agent.store.list_incidents()) == 6


# --------------------------------------------------------------------------- continuous loop (FR-004)


def test_run_loop_survives_a_crashing_cycle_and_stops_on_request(agent: Agent, monkeypatch):
    calls = {"n": 0}
    real = agent.run_once

    def sometimes_crash(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected")
        return real(**kwargs)

    monkeypatch.setattr(agent, "run_once", sometimes_crash)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda timeout=None: None)  # do not actually sleep
    assert agent.run(stop, max_cycles=3) == 3
    assert len(agent.store.list_incidents()) == 6
    assert any(e.action == "run_cycle" and not e.success for e in agent.store.list_audit_events())


def test_run_loop_waits_the_configured_interval(agent: Agent, monkeypatch, settings: Settings):
    waits: list[float] = []
    stop = threading.Event()

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 2:
            stop.set()

    monkeypatch.setattr(stop, "wait", fake_wait)
    assert agent.run(stop) == 2
    assert waits == [settings.polling.interval_seconds] * 2
