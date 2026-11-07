"""MCP servers (SRS Part 8; NFR-011/017; scenarios "MCP write operations disabled",
"MCP write operations enabled but unauthorized"). Calls go through the real MCP client
connected in-process, so argument schemas and result serialisation are exercised too."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client

from oncall_agent.agent import Agent
from oncall_agent.config import load_settings
from oncall_agent.factory import Integrations
from oncall_agent.integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from oncall_agent.logging_config import register_secrets
from oncall_agent.mcp_servers import airflow_server, linear_server
from oncall_agent.mcp_servers.common import Runtime
from oncall_agent.models import ApprovalStatus, AuditEventType, IncidentStatus
from oncall_agent.providers.mock import MockAIProvider
from oncall_agent.services.audit import AuditService
from oncall_agent.store import Store

from ..conftest import CONFIG_DIR, NO_ENV_FILE

pytestmark = pytest.mark.integration


def setup(tmp_path: Path, *, analyse: bool = True, **env) -> tuple[Runtime, Agent]:
    settings = load_settings({"APP_ENV": "development", "ONCALL_MAX_AUTO_RETRIES": "0", **env}, config_dir=CONFIG_DIR, env_file=NO_ENV_FILE)
    store = Store(tmp_path / "db.sqlite").open()
    integrations = Integrations(MockAirflowClient(), MockGitHubClient(), MockLinearClient(), mode="mock", ai=MockAIProvider())
    agent = Agent(settings, store, AuditService(store, tmp_path / "audit.jsonl"), integrations)
    if analyse:
        agent.run_once()  # retries disabled → nothing is cleared by the agent itself
        agent.shutdown()
    return Runtime.build(settings, integrations=integrations, store=store), agent


def call(server, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    async def run():
        async with Client(server) as client:
            result = await client.call_tool(tool, args or {})
            return result.structured_content

    return anyio.run(run)


def incident_id(rt: Runtime, dag_id: str) -> int:
    return next(i.id for i in rt.store.list_incidents() if i.dag_id == dag_id)


def mcp_audits(rt: Runtime) -> list:
    return [e for e in rt.store.list_audit_events() if e.event_type is AuditEventType.MCP_TOOL_CALL]


# --------------------------------------------------------------------------- airflow server


def test_airflow_tools_are_listed_and_read_tools_work(tmp_path):
    rt, _ = setup(tmp_path)
    server = airflow_server.build_server(rt)

    async def names():
        async with Client(server) as client:
            return sorted(t.name for t in (await client.list_tools()).tools)

    assert anyio.run(names) == ["clear_failed_task", "get_dag", "get_task_instances", "get_task_log", "get_task_state", "list_dag_runs", "list_dags"]
    assert len(call(server, "list_dags")["data"]) == 5
    assert call(server, "get_dag", {"dag_id": "customer_sync"})["data"]["owners"] == ["alice"]
    runs = call(server, "list_dag_runs", {"dag_id": "sales_etl", "state": "failed"})["data"]
    tis = call(server, "get_task_instances", {"dag_id": "sales_etl", "dag_run_id": runs[0]["dag_run_id"], "state": "upstream_failed"})["data"]
    assert {t["task_id"] for t in tis} == {"transform_orders", "load_orders"}
    state = call(server, "get_task_state", {"dag_id": "sales_etl", "dag_run_id": runs[0]["dag_run_id"], "task_id": "extract_orders"})["data"]
    assert state["state"] == "failed"
    log = call(server, "get_task_log", {"dag_id": "sales_etl", "dag_run_id": runs[0]["dag_run_id"], "task_id": "extract_orders"})["data"]
    assert "ReadTimeout" in log["log"]
    assert [e.action for e in mcp_audits(rt)] == ["get_task_log"]  # log access is audited


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_dag", {"dag_id": "../../etc/passwd"}),
        ("get_dag", {"dag_id": ""}),
        ("list_dag_runs", {"dag_id": "sales_etl", "limit": 0}),
        ("list_dag_runs", {"dag_id": "sales_etl", "limit": 1000}),
        ("get_task_log", {"dag_id": "d", "dag_run_id": "run id with spaces", "task_id": "t"}),
        ("get_task_log", {"dag_id": "d", "dag_run_id": "r", "task_id": "t", "try_number": 0}),
    ],
)
def test_invalid_arguments_are_rejected_with_structured_errors(tmp_path, tool, args):
    rt, _ = setup(tmp_path, analyse=False)
    result = call(airflow_server.build_server(rt), tool, args)
    assert result == {"ok": False, "error": {"code": "invalid_argument", "message": result["error"]["message"]}}
    assert mcp_audits(rt)[-1].success is False


def test_upstream_failure_is_a_structured_error(tmp_path):
    rt, _ = setup(tmp_path, analyse=False)
    rt.integrations.airflow.unavailable = True
    result = call(airflow_server.build_server(rt), "list_dags")
    assert result["ok"] is False and result["error"]["code"] == "upstream_error"


def test_airflow_writes_are_disabled_by_default(tmp_path):  # "MCP write operations disabled"
    rt, _ = setup(tmp_path)
    result = call(airflow_server.build_server(rt), "clear_failed_task", {"incident_id": incident_id(rt, "sales_etl")})
    assert result["error"]["code"] == "write_disabled"
    assert rt.integrations.airflow.write_calls == []
    assert mcp_audits(rt)[-1].action == "clear_failed_task" and not mcp_audits(rt)[-1].success


@pytest.mark.parametrize("dag_id", ["sales_etl", "customer_sync", "finance_ledger_close", "reporting_daily"])
def test_enabled_writes_still_need_the_policy(tmp_path, dag_id):  # "MCP write operations enabled but unauthorized"
    # Retries are disabled in config (limit 0), so even the retriable sales_etl is refused.
    rt, _ = setup(tmp_path, ONCALL_MCP_ALLOW_AIRFLOW_WRITES="true")
    before = rt.store.get_incident(incident_id(rt, dag_id))
    result = call(airflow_server.build_server(rt), "clear_failed_task", {"incident_id": before.id})
    assert result["error"]["code"] == "not_permitted" and "policy does not allow" in result["error"]["message"]
    assert rt.integrations.airflow.write_calls == []
    assert rt.store.get_incident(before.id).status is before.status  # a refused MCP call changes nothing


def test_enabled_writes_with_policy_approval_retry_once(tmp_path):
    rt, agent = setup(tmp_path, ONCALL_MCP_ALLOW_AIRFLOW_WRITES="true")
    # Re-open the policy for this test: retries allowed, incident back to investigating.
    rt.settings = rt.settings.model_copy(update={"policy": rt.settings.policy.model_copy(update={"max_auto_retries": 2})})
    rt.policy.settings = rt.settings.policy
    rt.recovery.settings = rt.settings.policy
    sales = rt.store.get_incident(incident_id(rt, "sales_etl"))
    rt.store.update_incident(sales.model_copy(update={"status": IncidentStatus.INVESTIGATING}))
    server = airflow_server.build_server(rt)
    first = call(server, "clear_failed_task", {"incident_id": sales.id})
    assert first["ok"] and first["data"]["cleared"] == ["extract_orders", "load_orders", "transform_orders"]
    second = call(server, "clear_failed_task", {"incident_id": sales.id})
    assert second["error"]["code"] == "not_permitted"  # incident now retrying: no second clear
    assert len(rt.integrations.airflow.write_calls) == 1


def test_unknown_incident_is_not_found_and_still_audited(tmp_path):
    rt, _ = setup(tmp_path, analyse=False, ONCALL_MCP_ALLOW_AIRFLOW_WRITES="true")
    result = call(airflow_server.build_server(rt), "clear_failed_task", {"incident_id": 999})
    assert result["error"]["code"] == "not_found"
    assert mcp_audits(rt)[-1].incident_id is None and mcp_audits(rt)[-1].action == "clear_failed_task"


def test_results_never_contain_secrets(tmp_path):  # §8.3 / NFR-009
    rt, _ = setup(tmp_path)
    register_secrets(["mcp-secret-value-xyz"])
    run = call(airflow_server.build_server(rt), "list_dag_runs", {"dag_id": "sales_etl", "state": "failed"})["data"][0]["dag_run_id"]
    rt.integrations.airflow.logs[("sales_etl", run, "extract_orders", 1)] += "\npassword=mcp-secret-value-xyz Authorization: Bearer tok_abcdefgh123"
    log = call(airflow_server.build_server(rt), "get_task_log", {"dag_id": "sales_etl", "dag_run_id": run, "task_id": "extract_orders"})["data"]["log"]
    assert "mcp-secret-value-xyz" not in log and "tok_abcdefgh123" not in log


def test_server_reuses_the_agents_clients(tmp_path):  # NFR-017
    rt, agent = setup(tmp_path, analyse=False)
    tools = airflow_server.AirflowTools(rt)
    assert tools.airflow is agent.integrations.airflow


# --------------------------------------------------------------------------- linear server


def test_linear_read_tools(tmp_path):
    rt, _ = setup(tmp_path)
    server = linear_server.build_server(rt)
    found = call(server, "search_issues", {"query": "customer_sync"})["data"]
    assert len(found) == 1 and "oncall" in found[0]["labels"]
    info = call(server, "get_approval_info", {"incident_id": incident_id(rt, "reporting_daily")})["data"]
    assert info["incident_status"] == "awaiting_approval" and info["approvals"][0]["status"] == "pending"
    assert "oncall:approved" in info["how_to_approve"]


@pytest.mark.parametrize(
    "tool,args",
    [
        ("create_incident", {"incident_id": 1}),
        ("update_issue", {"issue_id": "issue-1", "priority": 1}),
        ("add_comment", {"issue_id": "issue-1", "body": "hello"}),
        ("update_labels", {"issue_id": "issue-1", "add": ["oncall:escalated"]}),
        ("resolve_incident", {"incident_id": 1, "reason": "fixed"}),
    ],
)
def test_linear_writes_are_disabled_by_default(tmp_path, tool, args):
    rt, _ = setup(tmp_path)
    before = {k: v.model_dump() for k, v in rt.integrations.linear.issues.items()}
    assert call(linear_server.build_server(rt), tool, args)["error"]["code"] == "write_disabled"
    assert {k: v.model_dump() for k, v in rt.integrations.linear.issues.items()} == before


def test_linear_writes_when_enabled(tmp_path):
    rt, _ = setup(tmp_path, ONCALL_MCP_ALLOW_LINEAR_WRITES="true")
    server = linear_server.build_server(rt)
    ml = rt.store.get_incident(incident_id(rt, "ml_feature_build"))
    assert call(server, "update_issue", {"issue_id": ml.linear_issue_id, "priority": 1})["data"]["priority"] == 1
    assert call(server, "add_comment", {"issue_id": ml.linear_issue_id, "body": "Looking into it"})["ok"]
    assert "oncall:escalated" not in call(server, "update_labels", {"issue_id": ml.linear_issue_id, "remove": ["oncall:escalated"]})["data"]["labels"]
    resolved = call(server, "resolve_incident", {"incident_id": ml.id, "reason": "memory limit raised"})["data"]
    assert resolved["status"] == "resolved" and rt.integrations.linear.issues[ml.linear_issue_id].state.name == "Done"
    assert call(server, "create_incident", {"incident_id": ml.id})["data"]["linear_issue_id"] == ml.linear_issue_id  # no duplicate
    assert all(e.success for e in mcp_audits(rt))


@pytest.mark.parametrize(
    "tool,args",
    [
        ("add_comment", {"body": "/approve"}),
        ("add_comment", {"body": "  /APPROVE \nI checked it"}),
        ("update_labels", {"add": ["oncall:approved"]}),
    ],
)
def test_mcp_can_never_approve(tmp_path, tool, args):  # §8.3 "not automatic authorization"
    rt, agent = setup(tmp_path, ONCALL_MCP_ALLOW_LINEAR_WRITES="true")
    rd = rt.store.get_incident(incident_id(rt, "reporting_daily"))
    result = call(linear_server.build_server(rt), tool, {"issue_id": rd.linear_issue_id, **args})
    assert result["error"]["code"] == "not_permitted"
    agent.run_once()
    assert rt.store.list_approvals(rd.id)[0].status is ApprovalStatus.PENDING


def test_linear_validation(tmp_path):
    rt, _ = setup(tmp_path, ONCALL_MCP_ALLOW_LINEAR_WRITES="true")
    server = linear_server.build_server(rt)
    assert call(server, "update_issue", {"issue_id": "issue-1", "priority": 9})["error"]["code"] == "invalid_argument"
    assert call(server, "update_labels", {"issue_id": "issue-1", "add": ["no-such-label"]})["error"]["code"] == "invalid_argument"
    assert call(server, "add_comment", {"issue_id": "bad id!", "body": "x"})["error"]["code"] == "invalid_argument"
    assert call(server, "add_comment", {"issue_id": "issue-1", "body": "x\x00y"})["error"]["code"] == "invalid_argument"
    assert call(server, "search_issues", {"query": "", "limit": 5})["error"]["code"] == "invalid_argument"
