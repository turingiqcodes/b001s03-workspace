"""In-memory Airflow 3 for offline development, tests and simulation (NFR-030/031).

Mirrors the semantics the agent relies on:

* ``clear_task_instances`` scopes to one run, optionally adds downstream tasks, and with
  ``only_failed`` keeps only ``failed``/``upstream_failed`` instances. ``dry_run`` returns
  what would be cleared without changing anything.
* After a real clear the "re-run" outcome is controlled by ``retry_outcomes``
  (default ``success``), so tests can model a retry that succeeds, fails again, or
  stays running.

Every write is recorded in ``write_calls`` so simulations can prove they changed nothing.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from ...exceptions import AirflowError
from ..airflow import DagInfo, DagRun, ImportErrorInfo, TaskInstance
from .base import FaultInjector
from .sample_data import AirflowSample, build_airflow_sample

CLEARABLE_WHEN_ONLY_FAILED = {"failed", "upstream_failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MockAirflowClient(FaultInjector):
    error_cls = AirflowError

    def __init__(self, sample: AirflowSample | None = None) -> None:
        super().__init__()
        sample = sample if sample is not None else build_airflow_sample()
        self._lock = threading.RLock()
        self.dags: dict[str, DagInfo] = dict(sample.dags)
        self.structure = {k: dict(v) for k, v in sample.structure.items()}
        self.runs: dict[tuple[str, str], DagRun] = {(r.dag_id, r.dag_run_id): r for r in sample.runs}
        self.tis: dict[tuple[str, str, str, int], TaskInstance] = {(t.dag_id, t.dag_run_id, t.task_id, t.map_index): t for t in sample.task_instances}
        self.logs = dict(sample.logs)
        self.sources = dict(sample.sources)
        self.import_errors = list(sample.import_errors)
        # (dag_id, task_id) -> "success" | "failed" | "running"
        self.retry_outcomes: dict[tuple[str, str], str] = {}
        self.write_calls: list[tuple[str, dict[str, Any]]] = []
        self.version = "3.1.0-mock"

    def close(self) -> None:
        pass

    # ----------------------------------------------------------------- reads

    def check_connection(self) -> dict[str, Any]:
        self._enter("check_connection")
        return {"version": self.version, "dags_visible": len(self.dags)}

    def list_dags(self) -> list[DagInfo]:
        self._enter("list_dags")
        return sorted(self.dags.values(), key=lambda d: d.dag_id)

    def get_dag(self, dag_id: str) -> DagInfo:
        self._enter("get_dag", dag_id=dag_id)
        if dag_id not in self.dags:
            raise AirflowError(f"DAG {dag_id} not found", status_code=404)
        return self.dags[dag_id]

    def list_dag_runs(
        self,
        dag_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        order_by: str = "end_date",
        max_items: int | None = None,
    ) -> list[DagRun]:
        self._enter("list_dag_runs", dag_id=dag_id, end_date_gte=end_date_gte)
        with self._lock:
            runs = [
                r for r in self.runs.values()
                if (dag_id == "~" or r.dag_id == dag_id)
                and (not states or r.state in states)
                and (end_date_gte is None or (r.end_date is not None and r.end_date >= end_date_gte))
            ]  # fmt: skip
        field_name = order_by.lstrip("-")
        runs.sort(key=lambda r: getattr(r, field_name) or datetime.min.replace(tzinfo=timezone.utc), reverse=order_by.startswith("-"))
        return runs[:max_items] if max_items is not None else runs

    def get_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRun]:
        return self.list_dag_runs(dag_id, order_by="-start_date", max_items=limit)

    def get_dag_run(self, dag_id: str, dag_run_id: str) -> DagRun:
        self._enter("get_dag_run", dag_id=dag_id, dag_run_id=dag_run_id)
        run = self.runs.get((dag_id, dag_run_id))
        if run is None:
            raise AirflowError(f"DAG run {dag_id}/{dag_run_id} not found", status_code=404)
        return run

    def list_task_instances(
        self,
        dag_id: str = "~",
        dag_run_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        max_items: int | None = None,
    ) -> list[TaskInstance]:
        self._enter("list_task_instances", dag_id=dag_id, dag_run_id=dag_run_id, end_date_gte=end_date_gte)
        with self._lock:
            tis = [
                t for t in self.tis.values()
                if (dag_id == "~" or t.dag_id == dag_id)
                and (dag_run_id == "~" or t.dag_run_id == dag_run_id)
                and (not states or t.state in states)
                and (end_date_gte is None or (t.end_date is not None and t.end_date >= end_date_gte))
            ]  # fmt: skip
        tis.sort(key=lambda t: (t.end_date or datetime.min.replace(tzinfo=timezone.utc), t.dag_id, t.task_id))
        return tis[:max_items] if max_items is not None else tis

    def get_task_instance(self, dag_id: str, dag_run_id: str, task_id: str, map_index: int = -1) -> TaskInstance:
        self._enter("get_task_instance", dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id)
        ti = self.tis.get((dag_id, dag_run_id, task_id, map_index))
        if ti is None:
            raise AirflowError(f"Task instance {dag_id}/{dag_run_id}/{task_id} not found", status_code=404)
        return ti

    def get_task_log(self, dag_id: str, dag_run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str:
        self._enter("get_task_log", dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id, try_number=try_number)
        key = (dag_id, dag_run_id, task_id, try_number)
        if key not in self.logs:
            raise AirflowError(f"No log for {dag_id}/{dag_run_id}/{task_id} try {try_number}", status_code=404)
        return self.logs[key]

    def get_dag_source(self, dag_id: str) -> str:
        self._enter("get_dag_source", dag_id=dag_id)
        if dag_id not in self.sources:
            raise AirflowError(f"Source for {dag_id} not found", status_code=404)
        return self.sources[dag_id]

    def list_import_errors(self) -> list[ImportErrorInfo]:
        self._enter("list_import_errors")
        return sorted(self.import_errors, key=lambda e: e.timestamp)

    # ----------------------------------------------------------------- writes

    def _downstream(self, dag_id: str, task_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.structure.get(dag_id, {}).get(task_id, []))
        while stack:
            t = stack.pop()
            if t not in seen:
                seen.add(t)
                stack.extend(self.structure.get(dag_id, {}).get(t, []))
        return seen

    def clear_task_instances(
        self,
        dag_id: str,
        dag_run_id: str,
        task_ids: list[str],
        *,
        dry_run: bool,
        only_failed: bool = True,
        include_downstream: bool = False,
    ) -> list[TaskInstance]:
        self._enter("clear_task_instances", dag_id=dag_id, dag_run_id=dag_run_id, task_ids=task_ids, dry_run=dry_run)
        if (dag_id, dag_run_id) not in self.runs:
            raise AirflowError(f"DAG run {dag_id}/{dag_run_id} not found", status_code=404)
        with self._lock:
            wanted = set(task_ids)
            if include_downstream:
                for t in task_ids:
                    wanted |= self._downstream(dag_id, t)
            selected = [
                ti for key, ti in self.tis.items()
                if key[0] == dag_id and key[1] == dag_run_id and ti.task_id in wanted
                and (not only_failed or ti.state in CLEARABLE_WHEN_ONLY_FAILED)
            ]  # fmt: skip
            if dry_run:
                return selected
            self.write_calls.append(("clear_task_instances", {"dag_id": dag_id, "dag_run_id": dag_run_id, "task_ids": sorted(t.task_id for t in selected)}))
            self._rerun(dag_id, dag_run_id, selected, set(task_ids))
            return selected

    def _rerun(self, dag_id: str, dag_run_id: str, cleared: list[TaskInstance], targets: set[str]) -> None:
        """Model Airflow re-running the cleared tasks."""
        now = _now()
        outcomes = {self.retry_outcomes.get((dag_id, t), "success") for t in targets}
        target_outcome = "failed" if "failed" in outcomes else ("running" if "running" in outcomes else "success")
        downstream_outcome = {"success": "success", "failed": "upstream_failed", "running": None}[target_outcome]
        for ti in cleared:
            if ti.task_id in targets:
                outcome = self.retry_outcomes.get((dag_id, ti.task_id), "success")
            else:
                outcome = downstream_outcome
            self.tis[(ti.dag_id, ti.dag_run_id, ti.task_id, ti.map_index)] = ti.model_copy(
                update={"state": outcome, "try_number": ti.try_number + 1, "start_date": now, "end_date": None if outcome in (None, "running") else now}
            )
            if outcome == "failed":
                self.logs[(dag_id, dag_run_id, ti.task_id, ti.try_number + 1)] = self.logs.get((dag_id, dag_run_id, ti.task_id, ti.try_number), "ERROR - failed again")
        run = self.runs[(dag_id, dag_run_id)]
        states = {t.state for k, t in self.tis.items() if k[0] == dag_id and k[1] == dag_run_id}
        run_state = "running" if (None in states or "running" in states) else ("failed" if states & {"failed", "upstream_failed"} else "success")
        self.runs[(dag_id, dag_run_id)] = run.model_copy(update={"state": run_state, "end_date": None if run_state == "running" else now})

    def set_task_instance_state(
        self, dag_id: str, dag_run_id: str, task_id: str, new_state: str, *, map_index: int = -1, dry_run: bool
    ) -> list[TaskInstance]:
        self._enter("set_task_instance_state", dag_id=dag_id, dag_run_id=dag_run_id, task_id=task_id, new_state=new_state, dry_run=dry_run)
        key = (dag_id, dag_run_id, task_id, map_index)
        if key not in self.tis:
            raise AirflowError(f"Task instance {dag_id}/{dag_run_id}/{task_id} not found", status_code=404)
        if dry_run:
            return [self.tis[key]]
        self.write_calls.append(("set_task_instance_state", {"dag_id": dag_id, "dag_run_id": dag_run_id, "task_id": task_id, "new_state": new_state}))
        self.tis[key] = self.tis[key].model_copy(update={"state": new_state})
        return [self.tis[key]]

    # ----------------------------------------------------------------- test helpers

    def set_task_state(self, dag_id: str, dag_run_id: str, task_id: str, state: str | None) -> None:
        key = (dag_id, dag_run_id, task_id, -1)
        self.tis[key] = self.tis[key].model_copy(update={"state": state})
