"""Diagnostic context collection against the mock integrations.

AT-011, AT-012; FR-021..029; NFR-016/028; scenarios "Airflow unavailable",
"GitHub unavailable".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.agent import Agent
from oncall_agent.config import AISettings, GitHubSettings, load_settings
from oncall_agent.context import HISTORY_RUNS, ContextCollector, InvestigationContext
from oncall_agent.exceptions import AirflowError, GitHubError
from oncall_agent.factory import Integrations
from oncall_agent.integrations.github import CommitDetail, FileChange
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.models import AuditEventType, FailureType, Incident, IncidentStatus
from oncall_agent.services.audit import AuditService
from oncall_agent.services.investigation import InvestigationService
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.integration


@pytest.fixture
def airflow() -> MockAirflowClient:
    return MockAirflowClient()


@pytest.fixture
def github() -> MockGitHubClient:
    return MockGitHubClient()


def collector(airflow, github, **ai) -> ContextCollector:
    return ContextCollector(airflow, github, GitHubSettings(dag_path_prefix="dags/"), AISettings(**ai))


def failed(airflow: MockAirflowClient, dag_id: str) -> tuple[str, str, datetime]:
    ti = next(t for t in airflow.tis.values() if t.dag_id == dag_id and t.state == "failed")
    return ti.dag_run_id, ti.task_id, ti.end_date


def incident_for(airflow: MockAirflowClient, dag_id: str, error: str = "KeyError: 'customer_tier'", **overrides) -> Incident:
    run, task, end = failed(airflow, dag_id)
    data = dict(
        id=1, fingerprint="f" * 64, fingerprint_input={}, failure_type=FailureType.TASK_FAILURE, dag_id=dag_id, task_id=task,
        dag_run_id=run, original_error=error, normalized_error=error, last_detected_at=end, first_detected_at=end,
    )  # fmt: skip
    data.update(overrides)
    return Incident(**data)


# --------------------------------------------------------------------------- AT-011: everything available


def test_full_context_is_collected_when_available(airflow, github):  # AT-011
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"))
    assert "KeyError: 'customer_tier'" in ctx.task_log  # FR-021
    assert "def customer_sync" in ctx.dag_source  # FR-022
    assert ctx.dag_owners == ["alice"] and ctx.dag_file == "customer_sync.py"
    assert len(ctx.recent_runs) == HISTORY_RUNS  # FR-023
    assert [r.task_state for r in ctx.recent_runs].count("failed") == 1  # FR-024
    assert ctx.recent_runs[0].state == "failed" and ctx.recent_runs[1].task_state == "success"
    assert ctx.commit_search.startswith("commits to dags/customer_sync.py since")  # FR-025
    commit = ctx.commits[0]
    assert commit.short_sha == "a1b2c3d" and commit.author_email == "alice@example.com" and commit.author_login == "alice-gh"
    assert commit.files[0].patch and "customer_tier" in commit.files[0].patch  # FR-026
    assert [c.short_sha for c in ctx.possibly_related_commits] == ["a1b2c3d"]  # FR-027
    assert any("heuristic, not confirmed" in o for o in ctx.observations)
    assert ctx.missing == []
    assert set(ctx.collected_sources) == {"task_log", "dag_source", "run_history", "commits"}


def test_known_log_is_reused_instead_of_downloaded(airflow, github):  # NFR-016
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"), known_log="already downloaded\nKeyError: 'customer_tier'")
    assert ctx.task_log.startswith("already downloaded")
    assert airflow.call_count("get_task_log") == 0


def test_log_is_downloaded_for_the_latest_try_when_not_known(airflow, github):
    ctx = collector(airflow, github).collect(incident_for(airflow, "sales_etl"))
    assert "ReadTimeout" in ctx.task_log
    assert [a["try_number"] for n, a in airflow.calls if n == "get_task_log"] == [1]


def test_no_commits_is_an_observation_not_a_conclusion(airflow, github):  # FR-029
    ctx = collector(airflow, github).collect(incident_for(airflow, "sales_etl"))
    assert ctx.commits == [] and "commits" in ctx.collected_sources
    assert any("not proof that nothing changed" in o for o in ctx.observations)


def test_unrelated_commit_is_listed_but_not_flagged(airflow, github):
    ctx = collector(airflow, github).collect(incident_for(airflow, "ml_feature_build", error="Task exited with return code -9"))
    assert [c.short_sha for c in ctx.commits] == ["9988776"]
    assert ctx.possibly_related_commits == []
    assert [m.source for m in ctx.missing] == ["patch:9988776:dags/ml_feature_build.py"]  # GitHub gave no patch


def test_import_error_context(airflow, github):
    err = airflow.import_errors[0]
    inc = Incident(id=9, fingerprint="e" * 64, fingerprint_input={}, failure_type=FailureType.IMPORT_ERROR, dag_id=err.filename,
                   original_error="SyntaxError: invalid syntax", normalized_error="SyntaxError: invalid syntax", last_detected_at=err.timestamp)  # fmt: skip
    ctx = collector(airflow, github).collect(inc)
    assert "SyntaxError" in ctx.task_log  # stack trace fetched from the import errors list
    assert {m.source: m.kind for m in ctx.missing} == {"dag_source": "not_applicable", "run_history": "not_applicable"}
    assert ctx.commit_search.startswith("commits to dags/broken_dag.py")


def test_dag_run_failure_has_no_task_log(airflow, github):
    run = next(r for (d, r), v in airflow.runs.items() if d == "reporting_daily" and v.state == "failed")
    inc = Incident(id=5, fingerprint="d" * 64, fingerprint_input={}, failure_type=FailureType.DAG_RUN_FAILURE, dag_id="reporting_daily",
                   dag_run_id=run, normalized_error="DAG run ended", last_detected_at=datetime.now(timezone.utc))  # fmt: skip
    ctx = collector(airflow, github).collect(inc)
    assert ctx.task_log is None and ctx.missing[0].kind == "not_applicable"
    assert len(ctx.recent_runs) == HISTORY_RUNS and all(r.task_state is None for r in ctx.recent_runs)
    assert ctx.summary()["not_applicable"] and ctx.summary()["missing"] == []


def test_long_logs_and_patches_are_truncated(airflow, github):
    big_patch = "+" + "x" * 5000
    github.commits = [CommitDetail(sha="b" * 40, message="big", committed_at=datetime.now(timezone.utc) - timedelta(hours=1),
                                   files=[FileChange(filename="dags/customer_sync.py", status="modified", patch=big_patch)])]  # fmt: skip
    ctx = collector(airflow, github, max_log_chars=1000, max_patch_chars=1000).collect(
        incident_for(airflow, "customer_sync"), known_log="line\n" * 2000 + "KeyError: 'customer_tier'"
    )
    assert ctx.task_log_truncated and ctx.task_log.endswith("KeyError: 'customer_tier'") and len(ctx.task_log) < 1100
    assert ctx.commits[0].files[0].patch_truncated and len(ctx.commits[0].files[0].patch) < 1100


# --------------------------------------------------------------------------- AT-012: missing evidence


def test_airflow_unavailable_is_recorded_as_missing_evidence(airflow, github):  # AT-012 / FR-028
    airflow.unavailable = True
    c = collector(airflow, github)
    ctx = c.collect(incident_for(airflow, "customer_sync"))
    sources = {m.source for m in ctx.missing}
    assert {"task_log", "dag_info", "dag_source", "run_history", "commits"} <= sources
    assert all(m.kind == "unavailable" for m in ctx.missing)
    assert ctx.task_log is None and ctx.dag_source is None and ctx.recent_runs == [] and ctx.commits == []
    assert {a for a, _ in c.integration_failures} >= {"airflow.get_task_log", "airflow.get_dag", "airflow.get_dag_source", "airflow.get_recent_dag_runs"}


def test_github_unavailable_keeps_airflow_evidence(airflow, github):  # "GitHub unavailable"
    github.unavailable = True
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"))
    assert ctx.task_log and ctx.dag_source and len(ctx.recent_runs) == HISTORY_RUNS
    assert [m.source for m in ctx.missing] == ["commits"] and ctx.commits == []
    assert ctx.possibly_related_commits == []


def test_one_patch_failing_keeps_the_commit(airflow, github):
    github.fail_next("get_commit", GitHubError("502", retriable=True))
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"))
    assert ctx.commits[0].files == []
    assert ctx.missing[0].source == "patch:a1b2c3d"


def test_missing_historical_task_state_is_noted(airflow, github):
    for _ in range(3):
        airflow.fail_next("get_task_instance", AirflowError("timeout", retriable=True))
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"), known_log="KeyError: 'customer_tier'")
    assert [r.task_state for r in ctx.recent_runs[:3]] == ["unknown"] * 3
    assert any("could not be read for 3 of 10" in o for o in ctx.observations)


def test_bug_in_one_step_does_not_lose_the_others(airflow, github, monkeypatch):
    c = collector(airflow, github)
    monkeypatch.setattr(c, "_run_history", lambda ctx, _: 1 / 0)
    ctx = c.collect(incident_for(airflow, "customer_sync"))
    assert any(m.source == "run_history" and "internal error" in m.reason for m in ctx.missing)
    assert ctx.task_log and ctx.dag_source and ctx.commits


def test_missing_evidence_is_never_reported_as_evidence(airflow, github):  # FR-029
    airflow.unavailable = True
    ctx = collector(airflow, github).collect(incident_for(airflow, "customer_sync"))
    summary = ctx.summary()
    assert summary["collected"] == [] and len(summary["missing"]) >= 4
    assert summary["possibly_related_commits"] == []


# --------------------------------------------------------------------------- investigation service and agent wiring


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "db.sqlite").open()
    yield s
    s.close()


def test_investigation_marks_status_and_audits(airflow, github, store: Store, tmp_path: Path):
    audit = AuditService(store, tmp_path / "audit.jsonl")
    inc = store.create_incident(incident_for(airflow, "customer_sync").model_copy(update={"id": None}))
    github.unavailable = True
    ctx = InvestigationService(collector(airflow, github), store, audit).investigate(inc)
    assert isinstance(ctx, InvestigationContext)
    assert store.get_incident(inc.id).status is IncidentStatus.INVESTIGATING
    types = [e.event_type for e in audit.history(inc.id)]
    assert types == [AuditEventType.INTEGRATION_FAILURE, AuditEventType.CONTEXT_COLLECTED]  # NFR-028
    collected = audit.history(inc.id)[-1]
    assert "commits: " in collected.output_summary and collected.details["observations"]


def make_agent(tmp_path: Path, airflow, github) -> Agent:
    settings = load_settings({"APP_ENV": "development"}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)
    store = Store(tmp_path / "agent.db").open()
    return Agent(settings, store, AuditService(store, tmp_path / "audit.jsonl"), Integrations(airflow, github, MockLinearClient(), mode="mock"))


def test_agent_investigates_new_incidents_once(tmp_path: Path, airflow, github):
    agent = make_agent(tmp_path, airflow, github)
    report = agent.run_once()
    assert len(report.contexts) == 6
    # Investigated, analysed and decided: none is left open or mid-investigation.
    assert not [i for i in agent.store.list_incidents() if i.status in (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)]
    assert airflow.call_count("get_task_log") == 4  # only at detection (NFR-016)
    customer = next(c for c in report.contexts.values() if c.dag_id == "customer_sync")
    assert customer.possibly_related_commits[0].short_sha == "a1b2c3d"
    assert agent.run_once().contexts == {}  # nothing new to investigate


def test_failed_investigation_does_not_affect_watermarks(tmp_path: Path, airflow, github, monkeypatch):
    agent = make_agent(tmp_path, airflow, github)
    monkeypatch.setattr(agent.investigation, "investigate", lambda incident, known_log=None: 1 / 0)
    report = agent.run_once()
    assert len(report.errors) == 6 and report.contexts == {}
    assert len(agent.store.all_watermarks()) == 3
    assert len(agent.store.list_incidents()) == 6
