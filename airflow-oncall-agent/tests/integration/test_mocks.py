"""Mock integrations (NFR-030/031): interface compatibility, sample data, clear
semantics used by the safety rules, and fault injection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oncall_agent.exceptions import AirflowError, GitHubError, LinearError
from oncall_agent.integrations.airflow import AirflowClient
from oncall_agent.integrations.github import GitHubClient
from oncall_agent.integrations.linear import LinearClient
from oncall_agent.integrations.mocks import AGENT_USER, MockAirflowClient, MockGitHubClient, MockLinearClient

pytestmark = pytest.mark.integration


@pytest.fixture
def airflow() -> MockAirflowClient:
    return MockAirflowClient()


def failed_run(airflow: MockAirflowClient, dag_id: str) -> str:
    return next(r.dag_run_id for r in airflow.list_dag_runs(dag_id, states=["failed"]))


def test_mocks_implement_the_client_interfaces():
    assert isinstance(MockAirflowClient(), AirflowClient)
    assert isinstance(MockGitHubClient(), GitHubClient)
    assert isinstance(MockLinearClient(), LinearClient)


# --------------------------------------------------------------------------- sample data (Part 14)


def test_sample_data_contains_every_scenario(airflow: MockAirflowClient):
    failed_tis = {(t.dag_id, t.task_id) for t in airflow.list_task_instances(states=["failed"])}
    assert failed_tis == {
        ("sales_etl", "extract_orders"),
        ("customer_sync", "transform_customers"),
        ("ml_feature_build", "build_features"),
        ("finance_ledger_close", "close_ledger"),
    }
    failed_runs = {r.dag_id for r in airflow.list_dag_runs(states=["failed"])}
    assert "reporting_daily" in failed_runs  # run failure without a failed task
    assert airflow.list_import_errors()[0].filename.endswith("broken_dag.py")


def test_sample_history_has_ten_recent_runs(airflow: MockAirflowClient):  # FR-023
    runs = airflow.get_recent_dag_runs("sales_etl", limit=10)
    assert len(runs) == 10 and runs[0].state == "failed"
    assert all(r.state == "success" for r in runs[1:])


def test_sample_logs_and_source_are_available(airflow: MockAirflowClient):
    run = failed_run(airflow, "sales_etl")
    assert "ReadTimeout" in airflow.get_task_log("sales_etl", run, "extract_orders", 1)
    assert "def sales_etl" in airflow.get_dag_source("sales_etl")
    with pytest.raises(AirflowError):  # missing log is an error, not empty text
        airflow.get_task_log("sales_etl", run, "load_orders", 1)


def test_watermark_style_filtering(airflow: MockAirflowClient):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert airflow.list_task_instances(states=["failed"], end_date_gte=future) == []
    assert airflow.list_dag_runs(states=["failed"], end_date_gte=future) == []


# --------------------------------------------------------------------------- clear semantics (FR-057..059)


def test_dry_run_clear_never_changes_state(airflow: MockAirflowClient):
    run = failed_run(airflow, "sales_etl")
    before = {t.task_id: t.state for t in airflow.list_task_instances("sales_etl", run)}
    selected = airflow.clear_task_instances("sales_etl", run, ["extract_orders"], dry_run=True, include_downstream=True)
    assert {t.task_id for t in selected} == {"extract_orders", "transform_orders", "load_orders"}
    assert {t.task_id: t.state for t in airflow.list_task_instances("sales_etl", run)} == before
    assert airflow.write_calls == []


def test_only_failed_clear_excludes_successful_tasks(airflow: MockAirflowClient):
    run = failed_run(airflow, "customer_sync")
    selected = airflow.clear_task_instances("customer_sync", run, ["fetch_customers", "transform_customers"], dry_run=True, include_downstream=True)
    assert "fetch_customers" not in {t.task_id for t in selected}  # it succeeded
    assert {t.state for t in selected} <= {"failed", "upstream_failed"}


def test_clear_is_scoped_to_one_run(airflow: MockAirflowClient):
    run = failed_run(airflow, "sales_etl")
    selected = airflow.clear_task_instances("sales_etl", run, ["extract_orders"], dry_run=True, only_failed=False)
    assert {t.dag_run_id for t in selected} == {run}


def test_real_clear_reruns_tasks_with_configured_outcomes(airflow: MockAirflowClient):
    run = failed_run(airflow, "sales_etl")
    airflow.clear_task_instances("sales_etl", run, ["extract_orders"], dry_run=False, include_downstream=True)
    states = {t.task_id: (t.state, t.try_number) for t in airflow.list_task_instances("sales_etl", run)}
    assert states == {"extract_orders": ("success", 2), "transform_orders": ("success", 1), "load_orders": ("success", 1)}
    assert airflow.write_calls[0][0] == "clear_task_instances"


@pytest.mark.parametrize("outcome,target_state,downstream_state", [("failed", "failed", "upstream_failed"), ("running", "running", None)])
def test_retry_outcomes_can_fail_again_or_keep_running(airflow: MockAirflowClient, outcome, target_state, downstream_state):
    run = failed_run(airflow, "sales_etl")
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = outcome
    airflow.clear_task_instances("sales_etl", run, ["extract_orders"], dry_run=False, include_downstream=True)
    ti = airflow.get_task_instance("sales_etl", run, "extract_orders")
    assert ti.state == target_state and ti.try_number == 2
    assert airflow.get_task_instance("sales_etl", run, "load_orders").state == downstream_state


def test_set_state_dry_run_does_not_write(airflow: MockAirflowClient):
    run = failed_run(airflow, "ml_feature_build")
    airflow.set_task_instance_state("ml_feature_build", run, "build_features", "success", dry_run=True)
    assert airflow.get_task_instance("ml_feature_build", run, "build_features").state == "failed"
    airflow.set_task_instance_state("ml_feature_build", run, "build_features", "success", dry_run=False)
    assert airflow.get_task_instance("ml_feature_build", run, "build_features").state == "success"
    assert len(airflow.write_calls) == 1


# --------------------------------------------------------------------------- fault injection


@pytest.mark.parametrize("cls,err", [(MockAirflowClient, AirflowError), (MockGitHubClient, GitHubError), (MockLinearClient, LinearError)])
def test_unavailable_mocks_raise_retriable_errors(cls, err):
    mock = cls()
    mock.unavailable = True
    with pytest.raises(err) as info:
        mock.check_connection()
    assert info.value.retriable


def test_fail_next_injects_one_failure(airflow: MockAirflowClient):
    airflow.fail_next("list_import_errors", AirflowError("boom", retriable=True))
    with pytest.raises(AirflowError):
        airflow.list_import_errors()
    assert airflow.list_import_errors()
    assert airflow.call_count("list_import_errors") == 2


# --------------------------------------------------------------------------- GitHub and Linear mocks


def test_github_mock_filters_by_path_and_hides_patches_in_summaries():
    gh = MockGitHubClient()
    commits = gh.list_commits(path="dags/customer_sync.py")
    assert [c.short_sha for c in commits] == ["a1b2c3d"]
    assert gh.get_commit("a1b2c3d").files[0].patch.startswith("@@")
    assert gh.get_commit("99887766").files[0].patch is None  # missing patch evidence


def test_linear_mock_records_who_changed_labels():
    linear = MockLinearClient()
    ids = linear.find_label_ids(["oncall", "oncall:approved"])
    issue = linear.create_issue(title="t", description="fp", label_ids=[ids["oncall"]])
    linear.update_issue(issue.id, add_label_ids=[ids["oncall:approved"]])  # agent adds it itself
    linear.add_label_as(issue.id, "oncall:approved", "user-alice")  # a human adds it
    actors = [(e.actor.id, e.added_label_ids) for e in linear.list_label_events(issue.id)]
    assert actors == [(AGENT_USER.id, [ids["oncall"]]), (AGENT_USER.id, [ids["oncall:approved"]]), ("user-alice", [ids["oncall:approved"]])]


def test_linear_mock_comments_have_authors_and_search_works():
    linear = MockLinearClient()
    issue = linear.create_issue(title="[oncall] x", description="fingerprint: abc123")
    linear.comment_as(issue.identifier, "/approve", "user-bob")
    assert linear.list_comments(issue.id)[0].user.id == "user-bob"
    assert linear.search_issues("abc123")[0].id == issue.id
    assert linear.find_user("alice@example.com").id == "user-alice"
    assert linear.find_label_ids(["does-not-exist"]) == {}
