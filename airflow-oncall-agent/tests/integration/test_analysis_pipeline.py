"""Analysis inside the agent cycle (FR-040/041/042, NFR-018; AT-013, AT-015, AT-016).

Covers: every new incident analysed and stored; AI outage → fallback for all; analysis
off the polling path (slow AI does not block polling or watermarks); no double
submission; requeue after a crash; no Airflow writes from analysis.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from oncall_agent.agent import Agent
from oncall_agent.config import load_settings
from oncall_agent.exceptions import StoreError
from oncall_agent.factory import Integrations
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.models import AnalysisSource, Classification, IncidentStatus, ProposedAction
from oncall_agent.providers.mock import MockAIProvider
from oncall_agent.services.audit import AuditService
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.integration


class SlowAI(MockAIProvider):
    """Blocks every completion until released."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.started = 0

    def complete(self, prompt):
        self.started += 1
        assert self.release.wait(10), "test never released the slow provider"
        return super().complete(prompt)


@pytest.fixture
def airflow() -> MockAirflowClient:
    return MockAirflowClient()


def make_agent(tmp_path: Path, airflow, ai=None, **env) -> Agent:
    settings = load_settings({"APP_ENV": "development", **env}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)
    store = Store(tmp_path / "agent.db").open()
    integrations = Integrations(airflow, MockGitHubClient(), MockLinearClient(), mode="mock", ai=ai or MockAIProvider())
    return Agent(settings, store, AuditService(store, tmp_path / "audit.jsonl"), integrations)


def by_dag(agent: Agent):
    return {i.dag_id: i for i in agent.store.list_incidents()}


def test_every_new_incident_is_analysed_and_stored(tmp_path, airflow):  # AT-013 end to end
    agent = make_agent(tmp_path, airflow)
    report = agent.run_once()
    assert len(report.analyses) == 6 and report.ok
    incidents = by_dag(agent)
    assert incidents["sales_etl"].classification is Classification.RETRIABLE
    assert incidents["sales_etl"].proposed_action is ProposedAction.RETRY_TASK
    assert incidents["customer_sync"].suspected_commit.startswith("a1b2c3d")
    assert incidents["reporting_daily"].proposed_action is ProposedAction.RERUN_DAG
    assert all(i.analysis_source is AnalysisSource.AI for i in incidents.values())
    agent.shutdown()


def test_ai_outage_falls_back_for_every_incident(tmp_path, airflow):  # AT-015 / AT-016 end to end
    ai = MockAIProvider()
    ai.unavailable = True
    agent = make_agent(tmp_path, airflow, ai)
    report = agent.run_once()
    assert len(report.analyses) == 6 and report.ok  # the outage is handled, not an error
    for incident in agent.store.list_incidents():
        assert incident.analysis_source is AnalysisSource.FALLBACK
        assert incident.confidence <= 0.5 and incident.proposed_action is ProposedAction.MANUAL_INVESTIGATION
    agent.shutdown()


def test_ai_retry_proposal_alone_never_writes_to_airflow(tmp_path, airflow):  # FR-040 / FR-041 / FR-049
    agent = make_agent(tmp_path, airflow, ONCALL_MAX_AUTO_RETRIES="0")  # policy forbids retries
    agent.run_once()
    sales = by_dag(agent)["sales_etl"]
    assert sales.proposed_action is ProposedAction.RETRY_TASK and sales.confidence >= 0.7  # AI says retry...
    assert sales.status is IncidentStatus.ESCALATED  # ...the policy says no
    assert airflow.write_calls == []
    assert not any(name in ("clear_task_instances", "set_task_instance_state") for name, _ in airflow.calls)
    agent.shutdown()


def test_slow_ai_does_not_block_polling(tmp_path, airflow):  # NFR-018
    ai = SlowAI()
    agent = make_agent(tmp_path, airflow, ai)
    started = time.monotonic()
    report = agent.run_once(wait_for_analysis=False)
    assert time.monotonic() - started < 2
    assert len(agent.store.list_incidents()) == 6 and len(agent.store.all_watermarks()) == 3
    assert report.analyses == {} and len(agent.inflight) == 6

    second = agent.run_once(wait_for_analysis=False)  # still blocked: nothing submitted twice
    assert second.submitted == [] and len(agent.inflight) == 6

    ai.release.set()
    drained = agent.drain(timeout=10)
    assert len(drained.analyses) == 6 and agent.inflight == []
    assert len(ai.prompts) == 6  # each incident analysed exactly once
    agent.shutdown()


def test_finished_analyses_are_collected_by_a_later_cycle(tmp_path, airflow):
    agent = make_agent(tmp_path, airflow)
    agent.run_once(wait_for_analysis=False)
    deadline = time.monotonic() + 10
    while agent.inflight and any(not f.done() for f in agent._inflight.values()) and time.monotonic() < deadline:
        time.sleep(0.01)
    report = agent.run_once(wait_for_analysis=False)
    assert len(report.analyses) == 6 and agent.inflight == []
    agent.shutdown()


def test_unanalysed_incidents_are_requeued_next_cycle(tmp_path, airflow, monkeypatch):  # restart / crash recovery
    agent = make_agent(tmp_path, airflow)
    real = agent.analysis.analyze

    def broken(incident, ctx):
        raise StoreError("database is locked")

    monkeypatch.setattr(agent.analysis, "analyze", broken)
    first = agent.run_once()
    assert len(first.errors) == 6 and all(i.classification is None for i in agent.store.list_incidents())
    assert all(i.status is IncidentStatus.INVESTIGATING for i in agent.store.list_incidents())

    monkeypatch.setattr(agent.analysis, "analyze", real)
    second = agent.run_once()  # nothing new from Airflow, but the 6 are picked up again
    assert sorted(second.submitted) == sorted(i.id for i in agent.store.list_incidents())
    assert all(i.classification is not None for i in agent.store.list_incidents())
    agent.shutdown()


def test_restarted_agent_analyses_incidents_left_behind(tmp_path, airflow):
    first = make_agent(tmp_path, airflow, MockAIProvider())
    first.run_once(wait_for_analysis=False)
    first.shutdown(wait_for_analysis=False)  # "crash": queued work cancelled
    first.store.close()
    second = make_agent(tmp_path, airflow)
    second.run_once()
    assert all(i.classification is not None for i in second.store.list_incidents())
    second.shutdown()


def test_reopened_incident_is_reanalysed_not_judged_on_stale_analysis(tmp_path, airflow):
    agent = make_agent(tmp_path, airflow)
    agent.run_once()
    inc = by_dag(agent)["sales_etl"]
    agent.store.update_incident(agent.store.get_incident(inc.id).model_copy(update={"status": IncidentStatus.RESOLVED}))
    from datetime import datetime, timedelta, timezone

    from .test_monitoring import add_failed_run

    add_failed_run(airflow, "sales_etl", "extract_orders", datetime.now(timezone.utc) + timedelta(seconds=1), "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.shop.example', port=443): Read timed out. (read timeout=30)")
    agent.ai_seen = len(agent.analysis.provider.prompts)
    report = agent.run_once()
    assert report.outcomes["reopened"] == 1 and inc.id in report.analyses
    assert len(agent.analysis.provider.prompts) == agent.ai_seen + 1  # analysed again
    agent.shutdown()


def test_run_loop_drains_analyses_on_stop(tmp_path, airflow, monkeypatch):
    agent = make_agent(tmp_path, airflow)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda timeout=None: None)
    agent.run(stop, max_cycles=1)
    assert agent.inflight == [] and all(i.classification is not None for i in agent.store.list_incidents())
    agent.shutdown()
