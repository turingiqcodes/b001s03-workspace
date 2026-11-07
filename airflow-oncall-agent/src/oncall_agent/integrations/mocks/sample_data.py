"""Sample Airflow, GitHub and Linear data for offline development and simulation
(SRS Part 14). Timestamps are relative to ``now`` so the data is always "recent".

Scenarios
---------
=====================  =====================================================================
DAG                    What happened
=====================  =====================================================================
sales_etl              ``extract_orders`` timed out calling an upstream API (transient →
                       retriable). Downstream tasks are ``upstream_failed``. 9 previous runs
                       succeeded.
customer_sync          ``transform_customers`` raised ``KeyError: 'customer_tier'`` right
                       after commit ``a1b2c3d4…`` renamed the field (non-retriable, code bug).
ml_feature_build       ``build_features`` was killed with return code -9, no traceback
                       (uncertain).
finance_ledger_close   ``close_ledger`` hit a database lock timeout; the DAG is configured as
                       no-retry in config/development.json.
reporting_daily        DAG run failed (dagrun_timeout) with no failed task.
dags/broken_dag.py     Import error: SyntaxError.
=====================  =====================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..airflow import DagInfo, DagRun, ImportErrorInfo, TaskInstance
from ..github import CommitDetail, FileChange


@dataclass
class AirflowSample:
    dags: dict[str, DagInfo] = field(default_factory=dict)
    # dag_id -> task_id -> downstream task ids
    structure: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    runs: list[DagRun] = field(default_factory=list)
    task_instances: list[TaskInstance] = field(default_factory=list)
    logs: dict[tuple[str, str, str, int], str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    import_errors: list[ImportErrorInfo] = field(default_factory=list)


def _run_id(ts: datetime) -> str:
    return f"scheduled__{ts.strftime('%Y-%m-%dT%H:%M:%S')}+00:00"


def _log(lines: list[str]) -> str:
    return "\n".join(lines)


def _add_dag(sample: AirflowSample, dag_id: str, owners: list[str], tasks: dict[str, list[str]], source: str) -> None:
    sample.dags[dag_id] = DagInfo(dag_id=dag_id, owners=owners, is_paused=False, fileloc=f"/opt/airflow/dags/{dag_id}.py", relative_fileloc=f"{dag_id}.py")
    sample.structure[dag_id] = tasks
    sample.sources[dag_id] = source


def _add_history(sample: AirflowSample, dag_id: str, now: datetime, count: int = 9) -> None:
    """Successful earlier runs, one per 'day', for FR-023/024 context."""
    for day in range(count, 0, -1):
        start = now - timedelta(days=day)
        run = _run_id(start)
        sample.runs.append(DagRun(dag_id=dag_id, dag_run_id=run, state="success", run_type="scheduled", logical_date=start, start_date=start, end_date=start + timedelta(minutes=10)))
        for task in sample.structure[dag_id]:
            sample.task_instances.append(TaskInstance(dag_id=dag_id, dag_run_id=run, task_id=task, state="success", try_number=1, logical_date=start, start_date=start, end_date=start + timedelta(minutes=3)))


def _add_failed_run(
    sample: AirflowSample,
    dag_id: str,
    start: datetime,
    states: dict[str, str],
    failed_logs: dict[str, str],
    run_state: str = "failed",
) -> str:
    run = _run_id(start)
    end = start + timedelta(minutes=5)
    sample.runs.append(DagRun(dag_id=dag_id, dag_run_id=run, state=run_state, run_type="scheduled", logical_date=start, start_date=start, end_date=end))
    for task, state in states.items():
        ran = state in ("success", "failed")
        sample.task_instances.append(
            TaskInstance(
                dag_id=dag_id, dag_run_id=run, task_id=task, state=state, try_number=1 if ran else 0,
                logical_date=start, start_date=start if ran else None, end_date=end if state != "running" else None, operator="PythonOperator",
            )
        )  # fmt: skip
    for task, text in failed_logs.items():
        sample.logs[(dag_id, run, task, 1)] = text
    return run


def build_airflow_sample(now: datetime | None = None) -> AirflowSample:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    failed_at = now - timedelta(minutes=20)
    s = AirflowSample()

    # 1. sales_etl — transient upstream timeout (retriable)
    _add_dag(s, "sales_etl", ["data-eng"], {"extract_orders": ["transform_orders"], "transform_orders": ["load_orders"], "load_orders": []},
             'from airflow.sdk import dag, task\n\n@dag(schedule="@daily")\ndef sales_etl():\n    @task(retries=0)\n    def extract_orders():\n        return requests.get("https://api.shop.example/orders", timeout=30).json()\n    ...\n')  # fmt: skip
    _add_history(s, "sales_etl", now)
    _add_failed_run(s, "sales_etl", failed_at, {"extract_orders": "failed", "transform_orders": "upstream_failed", "load_orders": "upstream_failed"}, {
        "extract_orders": _log([
            f"[{failed_at.isoformat()}] INFO - Starting attempt 1 of 1",
            f"[{failed_at.isoformat()}] INFO - GET https://api.shop.example/orders?since=2026-11-04",
            "Traceback (most recent call last):",
            '  File "/opt/airflow/dags/sales_etl.py", line 7, in extract_orders',
            "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.shop.example', port=443): Read timed out. (read timeout=30)",
            f"[{(failed_at + timedelta(seconds=31)).isoformat()}] ERROR - Task failed with exception",
        ])})  # fmt: skip

    # 2. customer_sync — code bug introduced by a recent commit (non-retriable)
    _add_dag(s, "customer_sync", ["alice"], {"fetch_customers": ["transform_customers"], "transform_customers": ["publish_customers"], "publish_customers": []},
             'from airflow.sdk import dag, task\n\n@dag(schedule="@hourly")\ndef customer_sync():\n    @task\n    def transform_customers(rows):\n        return [{"id": r["id"], "tier": r["customer_tier"]} for r in rows]\n    ...\n')  # fmt: skip
    _add_history(s, "customer_sync", now)
    _add_failed_run(s, "customer_sync", failed_at - timedelta(minutes=2), {"fetch_customers": "success", "transform_customers": "failed", "publish_customers": "upstream_failed"}, {
        "transform_customers": _log([
            "INFO - Starting attempt 1 of 1",
            "Traceback (most recent call last):",
            '  File "/opt/airflow/dags/customer_sync.py", line 7, in transform_customers',
            "KeyError: 'customer_tier'",
            "ERROR - Task failed with exception",
        ])})  # fmt: skip

    # 3. ml_feature_build — killed, no traceback (uncertain)
    _add_dag(s, "ml_feature_build", ["ml-team"], {"build_features": ["train_model"], "train_model": []},
             'from airflow.sdk import dag, task\n\n@dag(schedule="@daily")\ndef ml_feature_build():\n    @task\n    def build_features():\n        ...\n')  # fmt: skip
    _add_history(s, "ml_feature_build", now)
    _add_failed_run(s, "ml_feature_build", failed_at - timedelta(minutes=4), {"build_features": "failed", "train_model": "upstream_failed"}, {
        "build_features": _log([
            "INFO - Starting attempt 1 of 1",
            "INFO - Loading 48,211,903 rows into memory at 0x7f3a2c1e4b50",
            "ERROR - Task exited with return code -9",
        ])})  # fmt: skip

    # 4. finance_ledger_close — transient, but the DAG is configured no-retry
    _add_dag(s, "finance_ledger_close", ["finance-eng"], {"close_ledger": []},
             'from airflow.sdk import dag, task\n\n@dag(schedule="0 2 * * *")\ndef finance_ledger_close():\n    @task\n    def close_ledger():\n        ...\n')  # fmt: skip
    _add_history(s, "finance_ledger_close", now)
    _add_failed_run(s, "finance_ledger_close", failed_at - timedelta(minutes=6), {"close_ledger": "failed"}, {
        "close_ledger": _log([
            "INFO - Starting attempt 1 of 1",
            "Traceback (most recent call last):",
            "psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout",
        ])})  # fmt: skip

    # 5. reporting_daily — DAG run failed with no failed task (dagrun_timeout)
    _add_dag(s, "reporting_daily", ["analytics"], {"generate_report": []},
             'from airflow.sdk import dag, task\n\n@dag(schedule="@daily", dagrun_timeout=timedelta(hours=1))\ndef reporting_daily():\n    ...\n')  # fmt: skip
    _add_history(s, "reporting_daily", now)
    _add_failed_run(s, "reporting_daily", failed_at - timedelta(minutes=8), {"generate_report": "skipped"}, {})

    # 6. import error
    s.import_errors.append(ImportErrorInfo(
        import_error_id=1, filename="/opt/airflow/dags/broken_dag.py", bundle_name="dags-folder", timestamp=failed_at - timedelta(minutes=10),
        stack_trace='Traceback (most recent call last):\n  File "/opt/airflow/dags/broken_dag.py", line 12\n    with DAG("broken_dag" as dag:\n                          ^\nSyntaxError: invalid syntax',
    ))  # fmt: skip
    return s


def build_github_sample(now: datetime | None = None) -> list[CommitDetail]:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    return [
        CommitDetail(
            sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            message="Rename tier column in customer_sync transform\n\nUpstream schema now uses 'tier'.",
            author_name="Alice Example", author_email="alice@example.com", author_login="alice-gh",
            committed_at=now - timedelta(hours=3), html_url="https://github.com/example-org/airflow-dags/commit/a1b2c3d",
            files=[FileChange(filename="dags/customer_sync.py", status="modified", additions=1, deletions=1,
                              patch='@@ -5,7 +5,7 @@ def customer_sync():\n     @task\n     def transform_customers(rows):\n-        return [{"id": r["id"], "tier": r["tier"]} for r in rows]\n+        return [{"id": r["id"], "tier": r["customer_tier"]} for r in rows]\n')],
        ),  # fmt: skip
        CommitDetail(
            sha="0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c",
            message="Bump requests timeout docs",
            author_name="Bob Example", author_email="bob@example.com", author_login="bob-gh",
            committed_at=now - timedelta(days=2), html_url="https://github.com/example-org/airflow-dags/commit/0f1e2d3",
            files=[FileChange(filename="docs/runbook.md", status="modified", additions=3, deletions=0, patch="@@ +1,3 @@\n+Timeouts...\n")],
        ),  # fmt: skip
        CommitDetail(
            sha="9988776655443322110099887766554433221100",
            message="Add feature columns to ml_feature_build (large binary fixture)",
            author_name="Carol Example", author_email="carol@example.com", author_login="carol-gh",
            committed_at=now - timedelta(days=1), html_url="https://github.com/example-org/airflow-dags/commit/9988776",
            files=[FileChange(filename="dags/ml_feature_build.py", status="modified", additions=40, deletions=2, patch=None)],
        ),  # fmt: skip
    ]
