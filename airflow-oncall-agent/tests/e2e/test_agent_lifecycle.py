"""End-to-end agent lifecycle against the mock integrations: detection → investigation →
analysis → policy → recovery → verification, across cycles and restarts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncall_agent.agent import Agent
from oncall_agent.config import load_settings
from oncall_agent.factory import Integrations
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.models import AnalysisSource, IncidentStatus
from oncall_agent.policy import Decision
from oncall_agent.providers.mock import MockAIProvider
from oncall_agent.services.audit import AuditService
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.e2e


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def make_agent(tmp_path: Path, airflow, *, ai=None, clock=None, **env) -> Agent:
    settings = load_settings({"APP_ENV": "development", **env}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)
    store = Store(tmp_path / "agent.db").open()
    integrations = Integrations(airflow, MockGitHubClient(), MockLinearClient(), mode="mock", ai=ai or MockAIProvider())
    return Agent(settings, store, AuditService(store, tmp_path / "audit.jsonl"), integrations, clock=clock)


def status(agent: Agent) -> dict[str, IncidentStatus]:
    return {i.dag_id: i.status for i in agent.store.list_incidents()}


def clears(airflow: MockAirflowClient) -> list:
    return [c for c in airflow.write_calls if c[0] == "clear_task_instances"]


def test_full_lifecycle_on_sample_scenarios(tmp_path):
    airflow = MockAirflowClient()
    agent = make_agent(tmp_path, airflow)
    first = agent.run_once()
    assert {agent.store.get_incident(i).dag_id: d.decision for i, d in first.decisions.items()} == {
        "sales_etl": Decision.AUTO_RETRY,
        "customer_sync": Decision.ESCALATE,
        "ml_feature_build": Decision.ESCALATE,
        "finance_ledger_close": Decision.ESCALATE,  # retriable, but a no-retry DAG
        "reporting_daily": Decision.REQUEST_APPROVAL,  # rerun_dag is restricted
        "/opt/airflow/dags/broken_dag.py": Decision.ESCALATE,
    }
    assert status(agent)["sales_etl"] is IncidentStatus.RETRYING
    assert [c[1]["dag_id"] for c in clears(airflow)] == ["sales_etl"]  # the only Airflow write

    second = agent.run_once()
    assert [v.outcome for v in second.verifications] == ["succeeded"]
    assert status(agent)["sales_etl"] is IncidentStatus.RESOLVED
    assert status(agent)["reporting_daily"] is IncidentStatus.AWAITING_APPROVAL
    assert len(airflow.write_calls) == 1
    agent.shutdown()


def test_retry_that_fails_again_is_escalated_and_not_retried_again(tmp_path):  # FR-063
    airflow = MockAirflowClient()
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "failed"
    clock = Clock()
    agent = make_agent(tmp_path, airflow, clock=clock)
    agent.run_once()
    clock.now += timedelta(minutes=1)
    second = agent.run_once()
    assert [v.outcome for v in second.verifications] == ["failed"]
    assert status(agent)["sales_etl"] is IncidentStatus.ESCALATED
    # The new failed try was detected as another occurrence of the same incident...
    sales = next(i for i in agent.store.list_incidents() if i.dag_id == "sales_etl")
    assert sales.occurrence_count == 2 and sales.retry_count == 1
    clock.now += timedelta(minutes=1)
    agent.run_once()
    assert len(clears(airflow)) == 1  # ...and was not retried again
    agent.shutdown()


def test_retry_still_running_after_30_minutes_is_escalated(tmp_path):  # FR-062
    airflow = MockAirflowClient()
    airflow.retry_outcomes[("sales_etl", "extract_orders")] = "running"
    clock = Clock()
    agent = make_agent(tmp_path, airflow, clock=clock)
    agent.run_once()
    clock.now += timedelta(minutes=10)
    assert [v.outcome for v in agent.run_once().verifications] == ["pending"]
    clock.now += timedelta(minutes=25)
    assert [v.outcome for v in agent.run_once().verifications] == ["timed_out"]
    assert status(agent)["sales_etl"] is IncidentStatus.ESCALATED
    agent.shutdown()


def test_ai_outage_means_no_automatic_action(tmp_path):  # AT-017 end to end
    airflow = MockAirflowClient()
    ai = MockAIProvider()
    ai.unavailable = True
    agent = make_agent(tmp_path, airflow, ai=ai)
    report = agent.run_once()
    assert all(i.analysis_source is AnalysisSource.FALLBACK for i in agent.store.list_incidents())
    assert all(d.decision is Decision.ESCALATE for d in report.decisions.values())
    assert airflow.write_calls == []
    agent.shutdown()


@pytest.mark.parametrize("env", [{"ONCALL_MAX_AUTO_RETRIES": "0"}, {"ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR": "0"}])
def test_zero_retry_limits_mean_no_airflow_writes(tmp_path, env):  # FR-051
    airflow = MockAirflowClient()
    agent = make_agent(tmp_path, airflow, **env)
    agent.run_once()
    assert airflow.write_calls == [] and status(agent)["sales_etl"] is IncidentStatus.ESCALATED
    agent.shutdown()


def test_restart_between_retry_and_verification(tmp_path):  # NFR-002, agent restart
    airflow = MockAirflowClient()
    first = make_agent(tmp_path, airflow)
    first.run_once()
    first.shutdown()
    first.store.close()
    second = make_agent(tmp_path, airflow)
    report = second.run_once()
    assert [v.outcome for v in report.verifications] == ["succeeded"]
    assert status(second)["sales_etl"] is IncidentStatus.RESOLVED
    assert len(clears(airflow)) == 1 and report.outcomes.get("created", 0) == 0
    second.shutdown()
