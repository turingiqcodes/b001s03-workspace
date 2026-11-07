"""CLI commands end to end (SRS Part 9): run, once, simulate, incidents, audit; exit codes."""
from __future__ import annotations

import io
import json

import pytest

from oncall_agent import simulate
from oncall_agent.agent import Agent
from oncall_agent.cli import EXIT_CONFIG, EXIT_FAILURE, EXIT_OK, main
from oncall_agent.policy import Decision

from ..conftest import NO_ENV_FILE

pytestmark = pytest.mark.e2e


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["--env-file", str(NO_ENV_FILE), *argv], out=out)
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ONCALL_LOG_LEVEL", "ERROR")


def test_once_runs_a_full_cycle_and_reports_decisions():
    code, text = run(["once"])
    assert code == EXIT_OK
    assert "created=6" in text and "auto_retry=1" in text and "linear_created=6" in text
    assert "sales_etl.extract_orders" in text and "→ retrying" in text and "→ awaiting_approval" in text


def test_once_refuses_to_start_without_production_credentials(monkeypatch):  # AT-002
    monkeypatch.setenv("APP_ENV", "production")
    code, text = run(["once"])
    assert code == EXIT_CONFIG and "will not start" in text and "AIRFLOW_BASE_URL" in text


def test_once_reports_failures_with_exit_code_1(monkeypatch):
    original = Agent.run_once

    def outage(self, **kw):
        self.integrations.airflow.unavailable = True
        return original(self, **kw)

    monkeypatch.setattr(Agent, "run_once", outage)
    code, text = run(["once"])
    assert code == EXIT_FAILURE and "polling failed" in text and "watermarks kept" in text


def test_incidents_table_filter_and_json():
    run(["once"])
    code, text = run(["incidents"])
    assert code == EXIT_OK and text.count("\n") == 7 and "awaiting_approval" in text and "ONC-" in text
    _, text = run(["incidents", "--status", "awaiting_approval"])
    assert text.count("\n") == 2 and "reporting_daily" in text
    _, text = run(["incidents", "--json", "--limit", "2"])
    data = json.loads(text)
    assert len(data) == 2 and {"id", "status", "fingerprint", "linear_issue_identifier"} <= set(data[0])


def test_incidents_when_empty():
    assert run(["incidents"]) == (EXIT_OK, "No incidents.\n")


def test_audit_history_of_one_incident():
    run(["once"])
    code, text = run(["audit", "1", "-v"])
    assert code == EXIT_OK
    for event in ("failure_detected", "incident_created", "context_collected", "policy_decision", "linear_issue_created"):
        assert event in text
    assert '"checks"' in text  # verbose details
    assert run(["audit", "999"])[0] == EXIT_FAILURE


def test_run_starts_monitoring_and_stops(monkeypatch):
    calls = {}

    def one_cycle(self, stop, max_cycles=None):
        calls["interval"] = self.settings.polling.interval_seconds
        self.run_once()
        return 1

    monkeypatch.setattr(Agent, "run", one_cycle)
    code, text = run(["run"])
    assert code == EXIT_OK and "Monitoring Airflow every 15s" in text and "Stopped after 1 cycle(s)" in text
    assert run(["incidents"])[1].count("\n") == 7


def test_simulate_runs_all_six_scenarios():
    code, text = run(["simulate"])
    assert code == EXIT_OK and "6/6 scenarios behaved as expected" in text
    for title in ("Retriable task failure", "Non-retriable task failure", "Uncertain failure", "Duplicate incident",
                  "AI provider unavailable", "Restricted operation requiring human approval"):  # fmt: skip
        assert f"{title}  ...  PASS" in text
    assert "No real system is contacted" in text
    assert "Investigation :" in text and "Policy        :" in text and "Expected      :" in text


def test_simulate_verbose_and_single_scenario():
    code, text = run(["simulate", "-v", "--scenario", "retriable"])
    assert code == EXIT_OK and "1/1 scenarios" in text
    assert "Policy checks :" in text and "[x] confidence_threshold" in text and "Explanation   :" in text


def test_simulate_never_uses_configured_integrations(monkeypatch, real_env):
    for k, v in real_env.items():
        monkeypatch.setenv(k, v)  # production-like config with real credentials
    code, text = run(["simulate", "--scenario", "uncertain"])
    assert code == EXIT_OK  # ran entirely on mocks; the network guard would have failed otherwise


def test_simulate_reports_unexpected_behaviour(monkeypatch):
    broken = [(k, t, d, Decision.AUTO_RETRY if k == "uncertain" else e, fn) for k, t, d, e, fn in simulate.SCENARIOS]
    monkeypatch.setattr(simulate, "SCENARIOS", broken)
    code, text = run(["simulate", "--scenario", "uncertain"])
    assert code == EXIT_FAILURE and "FAIL" in text and "expected decision auto_retry, got escalate" in text
