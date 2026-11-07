"""``oncall-airflow-mcp`` — controlled Airflow tools over MCP (SRS §8.1, §8.3).

Read tools: list DAGs, DAG details, DAG runs, task instances, task log, task state.
Write tool: ``clear_failed_task(incident_id)``, which is

* **disabled** unless ``ONCALL_MCP_ALLOW_AIRFLOW_WRITES=true``;
* identified by an agent *incident*, never by raw DAG/run/task ids, so it can only act
  on a failure the agent has analysed;
* checked against the central policy engine first — a refused request has no side
  effects — and then executed by ``RecoveryService.auto_retry``, which re-checks the
  policy, validates the dry run and reserves the retry atomically. Restricted actions
  (``rerun_dag``/``mark_success``) are not available here: they need Linear approval.

Every write attempt, refusal and error is audited. Logs are redacted and truncated.

Run: ``oncall-airflow-mcp`` (stdio transport). Logs go to stderr only.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..config import load_settings
from ..exceptions import ConfigError, PolicyDeniedError, WriteDisabledError
from ..logging_config import redact, setup_logging
from .common import DAG_ID, RUN_ID, TASK_ID, NotFound, Runtime, bounded_int, ident, tool_call

SERVER = "oncall-airflow-mcp"
MAX_LOG_CHARS = 20000


class AirflowTools:
    """Tool bodies, kept separate from MCP registration so they are easy to test."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.airflow = rt.integrations.airflow

    def _call(self, tool: str, args: dict[str, Any], fn: Callable[[], Any], *, write: bool = False) -> dict[str, Any]:
        return tool_call(self.rt, SERVER, tool, args, fn, write=write)

    def list_dags(self) -> dict[str, Any]:
        return self._call("list_dags", {}, lambda: [d.model_dump(mode="json") for d in self.airflow.list_dags()])

    def get_dag(self, dag_id: str) -> dict[str, Any]:
        def body() -> Any:
            return self.airflow.get_dag(ident("dag_id", dag_id, DAG_ID)).model_dump(mode="json")

        return self._call("get_dag", {"dag_id": dag_id}, body)

    def list_dag_runs(self, dag_id: str, state: str | None = None, limit: int = 25) -> dict[str, Any]:
        def body() -> Any:
            states = [ident("state", state, TASK_ID)] if state else None
            runs = self.airflow.list_dag_runs(ident("dag_id", dag_id, DAG_ID), states=states, order_by="-start_date", max_items=bounded_int("limit", limit, 1, 100))
            return [r.model_dump(mode="json") for r in runs]

        return self._call("list_dag_runs", {"dag_id": dag_id, "state": state, "limit": limit}, body)

    def get_task_instances(self, dag_id: str, dag_run_id: str, state: str | None = None) -> dict[str, Any]:
        def body() -> Any:
            states = [ident("state", state, TASK_ID)] if state else None
            tis = self.airflow.list_task_instances(ident("dag_id", dag_id, DAG_ID), ident("dag_run_id", dag_run_id, RUN_ID), states=states, max_items=500)
            return [t.model_dump(mode="json") for t in tis]

        return self._call("get_task_instances", {"dag_id": dag_id, "dag_run_id": dag_run_id, "state": state}, body)

    def get_task_log(self, dag_id: str, dag_run_id: str, task_id: str, try_number: int = 1) -> dict[str, Any]:
        def body() -> Any:
            text = self.airflow.get_task_log(
                ident("dag_id", dag_id, DAG_ID), ident("dag_run_id", dag_run_id, RUN_ID), ident("task_id", task_id, TASK_ID),
                bounded_int("try_number", try_number, 1, 1000),
            )  # fmt: skip
            truncated = len(text) > MAX_LOG_CHARS
            return {"log": redact(text[-MAX_LOG_CHARS:]), "truncated": truncated}

        # Logs can contain sensitive data: audited like a write.
        return self._call("get_task_log", {"dag_id": dag_id, "dag_run_id": dag_run_id, "task_id": task_id, "try_number": try_number}, body, write=True)

    def get_task_state(self, dag_id: str, dag_run_id: str, task_id: str) -> dict[str, Any]:
        def body() -> Any:
            ti = self.airflow.get_task_instance(ident("dag_id", dag_id, DAG_ID), ident("dag_run_id", dag_run_id, RUN_ID), ident("task_id", task_id, TASK_ID))
            return {"state": ti.state, "try_number": ti.try_number, "start_date": ti.start_date, "end_date": ti.end_date}

        return self._call("get_task_state", {"dag_id": dag_id, "dag_run_id": dag_run_id, "task_id": task_id}, body)

    def clear_failed_task(self, incident_id: int) -> dict[str, Any]:
        def body() -> Any:
            if not self.rt.settings.mcp.allow_airflow_writes:
                raise WriteDisabledError("Airflow write tools are disabled (ONCALL_MCP_ALLOW_AIRFLOW_WRITES=false)")
            incident = self.rt.store.get_incident(bounded_int("incident_id", incident_id, 1, 2**31))
            if incident is None:
                raise NotFound(f"incident {incident_id} does not exist")
            count = self.rt.store.count_automatic_retries_since(datetime.now(timezone.utc) - timedelta(hours=1))
            decision = self.rt.policy.evaluate(incident, count)
            if not decision.auto_retry_allowed:
                # Read-only refusal: an MCP request must not change the incident.
                raise PolicyDeniedError("the policy does not allow an automatic retry: " + "; ".join(decision.reasons))
            result = self.rt.recovery.auto_retry(incident)
            if result.status != "cleared":
                raise PolicyDeniedError(f"retry not performed ({result.status}): {result.detail}")
            return {"status": result.status, "cleared": result.cleared, "retry_id": result.retry_id}

        return self._call("clear_failed_task", {"incident_id": incident_id}, body, write=True)


def build_server(rt: Runtime) -> MCPServer:
    tools = AirflowTools(rt)
    server = MCPServer(SERVER, instructions="Read Airflow state for on-call investigation. Writes are restricted by the agent's policy engine.")

    @server.tool(description="List DAGs visible to the agent.")
    def list_dags() -> dict[str, Any]:
        return tools.list_dags()

    @server.tool(description="Get one DAG's details (owners, file, paused).")
    def get_dag(dag_id: str) -> dict[str, Any]:
        return tools.get_dag(dag_id)

    @server.tool(description="List recent runs of a DAG, optionally filtered by state.")
    def list_dag_runs(dag_id: str, state: str | None = None, limit: int = 25) -> dict[str, Any]:
        return tools.list_dag_runs(dag_id, state, limit)

    @server.tool(description="List task instances of a DAG run, optionally filtered by state.")
    def get_task_instances(dag_id: str, dag_run_id: str, state: str | None = None) -> dict[str, Any]:
        return tools.get_task_instances(dag_id, dag_run_id, state)

    @server.tool(description="Get a task try's log (redacted, last 20,000 characters).")
    def get_task_log(dag_id: str, dag_run_id: str, task_id: str, try_number: int = 1) -> dict[str, Any]:
        return tools.get_task_log(dag_id, dag_run_id, task_id, try_number)

    @server.tool(description="Get a task instance's current state and try number.")
    def get_task_state(dag_id: str, dag_run_id: str, task_id: str) -> dict[str, Any]:
        return tools.get_task_state(dag_id, dag_run_id, task_id)

    @server.tool(description="Retry the failed task of an agent incident, only if writes are enabled AND the agent's policy allows an automatic retry.")
    def clear_failed_task(incident_id: int) -> dict[str, Any]:
        return tools.clear_failed_task(incident_id)

    return server


def main() -> int:  # pragma: no cover - process entry point
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3
    setup_logging(settings.logging.level, "json", settings.logging.dir, secrets=settings.secret_values())
    rt = Runtime.build(settings)
    try:
        build_server(rt).run("stdio")
    finally:
        rt.close()
    return 0
