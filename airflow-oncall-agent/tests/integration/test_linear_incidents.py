"""Linear incident management and approvals through the agent.

FR-018, FR-061, FR-063, FR-066..078; AT-024; NFR-012/035; scenarios "Linear unavailable",
"Linear issue update failure", "Approval received more than once", "Invalid approval".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.agent import Agent
from oncall_agent.config import load_settings
from oncall_agent.exceptions import LinearError
from oncall_agent.factory import Integrations
from oncall_agent.integrations.airflow import DagRun, TaskInstance
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.models import ApprovalSource, ApprovalStatus, AuditEventType, IncidentStatus
from oncall_agent.providers.mock import MockAIProvider
from oncall_agent.reporting import fingerprint_marker
from oncall_agent.services.audit import AuditService
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.integration

RERUN_REPLY = (
    '{"classification": "uncertain", "confidence": 0.6, "root_cause": "Data contract unclear", "evidence": ["KeyError"],'
    ' "suggested_fix": "Re-run once upstream data is confirmed", "proposed_action": "rerun_dag", "suspected_commit": null,'
    ' "explanation": "A human should decide."}'
)


def make_agent(tmp_path: Path, *, airflow=None, linear=None, ai=None, db="agent.db", **env) -> Agent:
    settings = load_settings({"APP_ENV": "development", **env}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)
    store = Store(tmp_path / db).open()
    integrations = Integrations(airflow or MockAirflowClient(), MockGitHubClient(), linear or MockLinearClient(), mode="mock", ai=ai or MockAIProvider())
    return Agent(settings, store, AuditService(store, tmp_path / "audit.jsonl"), integrations)


def inc(agent: Agent, dag_id: str):
    return next(i for i in agent.store.list_incidents() if i.dag_id == dag_id)


def issue(agent: Agent, dag_id: str):
    return agent.integrations.linear.issues[inc(agent, dag_id).linear_issue_id]


def comments(agent: Agent, dag_id: str) -> list[str]:
    return [c.body for c in agent.integrations.linear.comments.get(inc(agent, dag_id).linear_issue_id, [])]


# --------------------------------------------------------------------------- issue creation (FR-074/076/077/078)


def test_issue_per_incident_with_labels_priority_owner_and_summary(tmp_path):
    agent = make_agent(tmp_path)
    report = agent.run_once()
    assert len(report.linear.created) == 6 and not report.linear.failed
    cs = issue(agent, "customer_sync")
    assert cs.priority == 2 and cs.label_names == {"oncall", "oncall:non-retriable", "oncall:escalated"}  # A7
    assert cs.assignee.email == "alice@example.com"  # author of the suspected commit
    assert inc(agent, "customer_sync").ownership_note.endswith("assigned to author of a1b2c3d (alice-gh)")
    assert issue(agent, "ml_feature_build").priority == 3 and issue(agent, "sales_etl").priority == 4
    assert issue(agent, "/opt/airflow/dags/broken_dag.py").label_names >= {"oncall:import-error"}
    assert issue(agent, "sales_etl").assignee.email == "bob@example.com"  # DAG owner data-eng via user_map
    assert issue(agent, "ml_feature_build").assignee is None  # nobody resolvable → unassigned, reason kept
    assert "left unassigned" in inc(agent, "ml_feature_build").ownership_note
    assert comments(agent, "customer_sync")[0].startswith("**Investigation summary**")
    assert fingerprint_marker(inc(agent, "customer_sync").fingerprint) in cs.description
    agent.shutdown()


def test_status_changes_are_mirrored(tmp_path):  # FR-061
    agent = make_agent(tmp_path)
    agent.run_once()
    assert issue(agent, "sales_etl").state.name == "In Progress"
    agent.run_once()  # retry verified → resolved
    sales = issue(agent, "sales_etl")
    assert sales.state.name == "Done" and comments(agent, "sales_etl")[-1].startswith("**Resolved.** retry succeeded")
    agent.shutdown()


def test_failed_retry_is_escalated_in_linear(tmp_path):  # FR-063
    airflow = MockAirflowClient()
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "failed"
    agent = make_agent(tmp_path, airflow=airflow)
    agent.run_once()
    agent.run_once()
    sales = issue(agent, "sales_etl")
    assert "oncall:escalated" in sales.label_names and sales.state.name == "Todo"
    assert any(c.startswith("**Escalated to a human.** retry did not fix the failure") for c in comments(agent, "sales_etl"))
    agent.shutdown()


def test_repeat_failure_updates_the_existing_issue(tmp_path):  # FR-075 / FR-018
    airflow = MockAirflowClient()
    agent = make_agent(tmp_path, airflow=airflow, ONCALL_MAX_AUTO_RETRIES="0")
    agent.run_once()
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    run_id = f"manual__{end.isoformat()}"
    airflow.runs[("customer_sync", run_id)] = DagRun(dag_id="customer_sync", dag_run_id=run_id, state="failed", end_date=end)
    airflow.tis[("customer_sync", run_id, "transform_customers", -1)] = TaskInstance(dag_id="customer_sync", dag_run_id=run_id, task_id="transform_customers", state="failed", try_number=1, end_date=end)
    airflow.logs[("customer_sync", run_id, "transform_customers", 1)] = "KeyError: 'customer_tier'"
    report = agent.run_once()
    assert report.linear.created == [] and len(agent.integrations.linear.issues) == 6
    assert any("occurred again (2 occurrences" in c for c in comments(agent, "customer_sync"))
    agent.shutdown()


# --------------------------------------------------------------------------- Linear failures


def test_linear_outage_does_not_stop_monitoring_and_is_retried(tmp_path):  # "Linear unavailable"
    linear = MockLinearClient()
    linear.unavailable = True
    agent = make_agent(tmp_path, linear=linear)
    report = agent.run_once()
    assert len(agent.store.list_incidents()) == 6 and len(agent.store.all_watermarks()) == 3
    assert len(report.linear.failed) == 6 and not linear.issues
    assert any(e.event_type is AuditEventType.INTEGRATION_FAILURE and e.action == "linear.sync" for e in agent.store.list_audit_events())
    linear.unavailable = False
    report = agent.run_once()
    assert len(report.linear.created) == 6 and len(linear.issues) == 6
    agent.shutdown()


def test_failed_issue_update_is_retried_next_cycle(tmp_path):  # "Linear issue update failure"
    linear = MockLinearClient()
    agent = make_agent(tmp_path, linear=linear)
    agent.run_once()
    linear.fail_next("update_issue", LinearError("500", retriable=True))
    report = agent.run_once()  # sales_etl resolved locally, Linear update fails
    assert inc(agent, "sales_etl").status is IncidentStatus.RESOLVED
    assert issue(agent, "sales_etl").state.name == "In Progress" and report.linear.failed
    agent.run_once()
    assert issue(agent, "sales_etl").state.name == "Done"
    agent.shutdown()


def test_existing_issue_is_relinked_after_database_loss(tmp_path):  # FR-018: no duplicate issue
    linear, airflow = MockLinearClient(), MockAirflowClient()
    first = make_agent(tmp_path, airflow=MockAirflowClient(), linear=linear, ONCALL_MAX_AUTO_RETRIES="0")
    first.run_once()
    first.shutdown()
    second = make_agent(tmp_path, airflow=airflow, linear=linear, db="fresh.db", ONCALL_MAX_AUTO_RETRIES="0")  # empty database
    report = second.run_once()
    assert len(report.linear.relinked) == 6 and report.linear.created == []
    assert len(linear.issues) == 6
    second.shutdown()


# --------------------------------------------------------------------------- approvals (FR-066..073, AT-024)


def cs_approvals(agent: Agent, report) -> list[str]:
    """Outcomes for the customer_sync approval only (reporting_daily also has one pending)."""
    ids = {a.id for a in agent.store.list_approvals(inc(agent, "customer_sync").id)}
    return [a.status for a in report.approvals if a.approval_id in ids]


def approval_agent(tmp_path, **env) -> Agent:
    ai = MockAIProvider()
    agent = make_agent(tmp_path, ai=ai, **env)
    original = ai.complete

    def complete(prompt):  # the AI proposes rerun_dag for customer_sync only
        return RERUN_REPLY if prompt.data["failure"]["dag_id"] == "customer_sync" else original(prompt)

    ai.complete = complete
    agent.run_once()
    assert inc(agent, "customer_sync").status is IncidentStatus.AWAITING_APPROVAL
    return agent


def test_label_approval_by_trusted_user_executes_once(tmp_path):  # AT-024 / FR-068 / FR-070 / FR-072
    agent = approval_agent(tmp_path)
    linear, airflow = agent.integrations.linear, agent.integrations.airflow
    issue_id = inc(agent, "customer_sync").linear_issue_id
    linear.add_label_as(issue_id, "oncall:approved", "user-alice")
    report = agent.run_once()
    assert cs_approvals(agent, report) == ["executed"]
    approval = agent.store.list_approvals(inc(agent, "customer_sync").id)[0]
    assert (approval.status, approval.approval_source, approval.approver) == (ApprovalStatus.EXECUTED, ApprovalSource.LINEAR_LABEL, "alice@example.com")
    assert approval.approved_at is not None and approval.executed_at is not None
    clears = [c for c in airflow.write_calls if c[1]["dag_id"] == "customer_sync"]
    assert clears == [("clear_task_instances", {"dag_id": "customer_sync", "dag_run_id": inc(agent, "customer_sync").dag_run_id, "task_ids": ["publish_customers", "transform_customers"]})]
    # Approval received more than once: a second label and a comment change nothing.
    linear.add_label_as(issue_id, "oncall:approved", "user-alice")
    linear.comment_as(issue_id, "/approve", "user-alice")
    agent.run_once()
    assert len([c for c in airflow.write_calls if c[1]["dag_id"] == "customer_sync"]) == 1
    assert inc(agent, "customer_sync").status is IncidentStatus.RESOLVED  # rerun verified
    agent.shutdown()


def test_comment_approval(tmp_path):  # FR-069
    agent = approval_agent(tmp_path)
    agent.integrations.linear.comment_as(inc(agent, "customer_sync").linear_issue_id, "/approve\nlooks safe", "user-alice")
    assert cs_approvals(agent, agent.run_once()) == ["executed"]
    assert agent.store.list_approvals(inc(agent, "customer_sync").id)[0].approval_source is ApprovalSource.LINEAR_COMMENT
    agent.shutdown()


@pytest.mark.parametrize(
    "act,reason",
    [
        (lambda l, i: l.comment_as(i, "/approve", "user-mallory"), "not a trusted approver"),
        (lambda l, i: l.add_label_as(i, "oncall:approved", "user-mallory"), "not a trusted approver"),
        (lambda l, i: l.update_issue(i, add_label_ids=[l.labels["oncall:approved"].id]), "agent's own Linear user"),  # e.g. via MCP
        (lambda l, i: l.add_comment(i, "/approve"), "agent's own Linear user"),
        (lambda l, i: l.comment_as(i, "please /approve this", "user-alice"), None),  # not the command: ignored silently
    ],
)
def test_invalid_approvals_never_execute(tmp_path, act, reason):  # "Invalid approval" / NFR-012 / FR-073
    agent = approval_agent(tmp_path)
    linear = agent.integrations.linear
    act(linear, inc(agent, "customer_sync").linear_issue_id)
    agent.run_once()
    agent.run_once()  # a rejection is reported only once
    assert not [c for c in agent.integrations.airflow.write_calls if c[1]["dag_id"] == "customer_sync"]
    assert agent.store.list_approvals(inc(agent, "customer_sync").id)[0].status is ApprovalStatus.PENDING
    rejected = [e for e in agent.store.list_audit_events() if e.event_type is AuditEventType.APPROVAL_REJECTED]
    if reason:
        assert len(rejected) == 1 and reason in rejected[0].output_summary
        assert sum("was ignored" in c for c in comments(agent, "customer_sync")) == 1
    else:
        assert rejected == []
    agent.shutdown()


def test_approval_before_the_request_does_not_count(tmp_path):
    agent = approval_agent(tmp_path)
    approval = agent.store.list_approvals(inc(agent, "customer_sync").id)[0]
    linear = agent.integrations.linear
    linear.comment_as(inc(agent, "customer_sync").linear_issue_id, "/approve", "user-alice")
    linear.comments[inc(agent, "customer_sync").linear_issue_id][-1] = linear.comments[inc(agent, "customer_sync").linear_issue_id][-1].model_copy(
        update={"created_at": approval.requested_at - timedelta(minutes=1)}
    )
    agent.run_once()
    assert agent.store.get_approval(approval.id).status is ApprovalStatus.PENDING
    agent.shutdown()


def test_empty_allowlist_rejects_everyone_outside_mock_mode(tmp_path):  # A2
    agent = approval_agent(tmp_path)
    agent.approvals.settings = agent.settings.model_copy(update={
        "integration_mode": "real", "approval": agent.settings.approval.model_copy(update={"trusted_approvers": ()}),
    })  # fmt: skip
    agent.integrations.linear.add_label_as(inc(agent, "customer_sync").linear_issue_id, "oncall:approved", "user-alice")
    agent.run_once()
    rejected = [e for e in agent.store.list_audit_events() if e.event_type is AuditEventType.APPROVAL_REJECTED]
    assert "no trusted approvers are configured" in rejected[0].output_summary
    agent.shutdown()


def test_empty_allowlist_accepts_humans_in_mock_mode(tmp_path):
    agent = approval_agent(tmp_path, ONCALL_TRUSTED_APPROVERS="")
    agent.approvals.settings = agent.settings.model_copy(update={"approval": agent.settings.approval.model_copy(update={"trusted_approvers": ()})})
    agent.integrations.linear.comment_as(inc(agent, "customer_sync").linear_issue_id, "/approve", "user-bob")
    assert cs_approvals(agent, agent.run_once()) == ["executed"]
    agent.shutdown()


def test_linear_outage_during_approval_check_is_retried(tmp_path):
    agent = approval_agent(tmp_path)
    linear = agent.integrations.linear
    linear.add_label_as(inc(agent, "customer_sync").linear_issue_id, "oncall:approved", "user-alice")
    linear.fail_next("list_label_events", LinearError("503", retriable=True))
    assert cs_approvals(agent, agent.run_once()) == ["waiting"]
    assert cs_approvals(agent, agent.run_once()) == ["executed"]
    agent.shutdown()


def test_label_added_before_the_request_does_not_count(tmp_path):
    agent = approval_agent(tmp_path)
    approval = agent.store.list_approvals(inc(agent, "customer_sync").id)[0]
    linear, issue_id = agent.integrations.linear, inc(agent, "customer_sync").linear_issue_id
    linear.add_label_as(issue_id, "oncall:approved", "user-alice")
    linear.history[issue_id][-1] = linear.history[issue_id][-1].model_copy(update={"created_at": approval.requested_at - timedelta(minutes=1)})
    agent.run_once()
    assert agent.store.get_approval(approval.id).status is ApprovalStatus.PENDING
    assert not [c for c in agent.integrations.airflow.write_calls if c[1]["dag_id"] == "customer_sync"]
    agent.shutdown()


def test_approval_with_unknown_actor_is_rejected(tmp_path):  # e.g. a deleted user or an integration
    agent = approval_agent(tmp_path)
    linear, issue_id = agent.integrations.linear, inc(agent, "customer_sync").linear_issue_id
    linear.add_label_as(issue_id, "oncall:approved", "user-alice")
    linear.history[issue_id][-1] = linear.history[issue_id][-1].model_copy(update={"actor": None})
    agent.run_once()
    rejected = [e for e in agent.store.list_audit_events() if e.event_type is AuditEventType.APPROVAL_REJECTED]
    assert "identity is unknown" in rejected[0].output_summary
    assert agent.store.list_approvals(inc(agent, "customer_sync").id)[0].status is ApprovalStatus.PENDING
    agent.shutdown()
