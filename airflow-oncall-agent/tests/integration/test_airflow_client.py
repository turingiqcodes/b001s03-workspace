"""Real Airflow client against recorded Airflow 3 API v2 responses (NFR-033).

AT-003 (connect), FR-001/003/012/021..024, NFR-001/010/015; scenarios "Airflow
unavailable" and "Malformed API responses".
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from oncall_agent.config import AirflowSettings
from oncall_agent.exceptions import AirflowError, MalformedResponseError
from oncall_agent.integrations.airflow import AirflowClient, HttpAirflowClient

from .fakes import FakeServer, raise_connect, raise_timeout

pytestmark = pytest.mark.integration

BASE = "https://airflow.example.com"
RUN = "scheduled__2026-11-05T06:00:00+00:00"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("oncall_agent.integrations.http.time.sleep", lambda s: None)


def jwt(exp: float) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'sub': 'oncall', 'exp': exp})}.sig"


def client(server: FakeServer, **overrides) -> HttpAirflowClient:
    settings = AirflowSettings(**{"base_url": BASE, "token": "static-token-value", "page_size": 2, **overrides})
    return HttpAirflowClient(settings, transport=server.transport())


def ti(task_id: str, state: str = "failed", **extra) -> dict:
    return {"dag_id": "sales_etl", "dag_run_id": RUN, "task_id": task_id, "map_index": -1, "state": state, "try_number": 1,
            "start_date": "2026-11-05T06:00:00Z", "end_date": "2026-11-05T06:05:00Z", "operator": "PythonOperator", "id": "uuid", **extra}  # fmt: skip


def run(run_id: str, state: str = "failed", end: str = "2026-11-05T06:05:00Z") -> dict:
    return {"dag_id": "sales_etl", "dag_run_id": run_id, "state": state, "run_type": "scheduled",
            "logical_date": "2026-11-05T06:00:00Z", "start_date": "2026-11-05T06:00:00Z", "end_date": end, "conf": {}}  # fmt: skip


# --------------------------------------------------------------------------- connection and auth (FR-001/003, AT-003)


def test_check_connection_with_bearer_token():  # AT-003 (mocked HTTP)
    server = FakeServer().on("GET", "/api/v2/version", (200, {"version": "3.1.0", "git_version": None})).on(
        "GET", "/api/v2/dags", (200, {"dags": [], "total_entries": 7})
    )
    assert client(server).check_connection() == {"version": "3.1.0", "dags_visible": 7}
    assert all(r.headers["Authorization"] == "Bearer static-token-value" for r in server.requests)
    assert isinstance(client(server), AirflowClient)


def test_username_password_is_exchanged_for_a_token_once():
    token = jwt(time.time() + 3600)
    server = (
        FakeServer()
        .on("POST", "/auth/token", (201, {"access_token": token}))
        .on("GET", "/api/v2/dags/sales_etl", (200, {"dag_id": "sales_etl", "owners": ["data-eng"], "fileloc": "/x.py"}))
    )
    c = client(server, token=None, username="oncall", password="pw-123456")
    c.get_dag("sales_etl")
    c.get_dag("sales_etl")
    assert len(server.calls("POST", "/auth/token")) == 1
    assert server.body(server.calls("POST", "/auth/token")[0]) == {"username": "oncall", "password": "pw-123456"}
    assert server.calls("GET", "/api/v2/dags/sales_etl")[0].headers["Authorization"] == f"Bearer {token}"


def test_expired_token_is_refreshed_on_401():
    old, new = jwt(time.time() + 3600), jwt(time.time() + 7200)
    server = (
        FakeServer()
        .on("POST", "/auth/token", (201, {"access_token": old}), (201, {"access_token": new}))
        .on("GET", "/api/v2/dags/sales_etl", (401, {"detail": "expired"}), (200, {"dag_id": "sales_etl"}))
    )
    c = client(server, token=None, username="oncall", password="pw-123456")
    assert c.get_dag("sales_etl").dag_id == "sales_etl"
    assert len(server.calls("POST", "/auth/token")) == 2
    assert server.calls("GET", "/api/v2/dags/sales_etl")[-1].headers["Authorization"] == f"Bearer {new}"


def test_token_close_to_expiry_is_refreshed_before_use():
    server = (
        FakeServer()
        .on("POST", "/auth/token", (201, {"access_token": jwt(time.time() + 10)}), (201, {"access_token": jwt(time.time() + 3600)}))
        .on("GET", "/api/v2/dags/d", (200, {"dag_id": "d"}))
    )
    c = client(server, token=None, username="u", password="pw-123456")
    c.get_dag("d")  # first token expires in 10s (< 60s refresh margin)...
    c.get_dag("d")  # ...so the next request refreshes it first
    assert len(server.calls("POST", "/auth/token")) == 2
    assert len(server.calls("GET", "/api/v2/dags/d")) == 2  # no 401 round-trip needed


def test_invalid_credentials_raise_non_retriable_error():
    server = FakeServer().on("POST", "/auth/token", (401, {"detail": "Invalid credentials"}))
    with pytest.raises(AirflowError) as info:
        client(server, token=None, username="u", password="wrong-pass").get_dag("d")
    assert info.value.status_code == 401 and not info.value.retriable


def test_token_endpoint_without_access_token_is_malformed():
    server = FakeServer().on("POST", "/auth/token", (201, {"token": "x"}))
    with pytest.raises(MalformedResponseError):
        client(server, token=None, username="u", password="pw-123456").get_dag("d")


def test_static_token_401_is_not_retried():
    server = FakeServer().on("GET", "/api/v2/dags/d", (401, {"detail": "bad token"}))
    with pytest.raises(AirflowError) as info:
        client(server).get_dag("d")
    assert info.value.status_code == 401 and len(server.requests) == 1


# --------------------------------------------------------------------------- reads, pagination (FR-005..008, NFR-015)


def test_list_dag_runs_paginates_and_sends_filters():
    server = FakeServer().on(
        "GET", "/api/v2/dags/~/dagRuns",
        (200, {"dag_runs": [run("r1"), run("r2")], "total_entries": 5}),
        (200, {"dag_runs": [run("r3"), run("r4")], "total_entries": 5}),
        (200, {"dag_runs": [run("r5")], "total_entries": 5}),
    )  # fmt: skip
    since = datetime(2026, 11, 5, 5, 0, tzinfo=timezone.utc)
    runs = client(server).list_dag_runs(states=["failed"], end_date_gte=since)
    assert [r.dag_run_id for r in runs] == ["r1", "r2", "r3", "r4", "r5"]
    offsets = [r.url.params["offset"] for r in server.requests]
    assert offsets == ["0", "2", "4"]
    first = server.requests[0].url.params
    assert first["state"] == "failed" and first["end_date_gte"] == "2026-11-05T05:00:00+00:00" and first["limit"] == "2"
    assert runs[0].end_date.tzinfo is not None


def test_pagination_respects_max_items():
    server = FakeServer().on("GET", "/api/v2/dags/sales_etl/dagRuns", (200, {"dag_runs": [run("a"), run("b")], "total_entries": 50}))
    runs = client(server).get_recent_dag_runs("sales_etl", limit=3)
    assert len(runs) == 3 and len(server.requests) == 2
    assert server.requests[0].url.params["order_by"] == "-start_date"
    assert server.requests[1].url.params["limit"] == "1"


def test_list_task_instances_across_all_dags_and_runs():
    server = FakeServer().on("GET", "/api/v2/dags/~/dagRuns/~/taskInstances", (200, {"task_instances": [ti("extract_orders")], "total_entries": 1}))
    tis = client(server).list_task_instances(states=["failed"])
    assert tis[0].task_id == "extract_orders" and tis[0].try_number == 1


def test_run_ids_are_quoted_in_paths():
    server = FakeServer().on("GET", f"/api/v2/dags/sales_etl/dagRuns/{RUN}/taskInstances/extract_orders", (200, ti("extract_orders")))
    client(server).get_task_instance("sales_etl", RUN, "extract_orders")
    assert "%2B00%3A00" in str(server.requests[0].url.raw_path)


def test_get_dag_run():
    server = FakeServer().on("GET", f"/api/v2/dags/sales_etl/dagRuns/{RUN}", (200, run(RUN, state="success")))
    assert client(server).get_dag_run("sales_etl", RUN).state == "success"


def test_naive_api_datetimes_are_treated_as_utc():
    server = FakeServer().on("GET", "/api/v2/dags/~/dagRuns", (200, {"dag_runs": [run("r1", end="2026-11-05T06:05:00")], "total_entries": 1}))
    assert client(server).list_dag_runs()[0].end_date.tzinfo == timezone.utc


def test_structured_logs_are_flattened_with_exception_details():  # FR-021
    content = [
        {"timestamp": "2026-11-05T06:00:01Z", "event": "Starting attempt 1 of 1", "level": "info"},
        {"timestamp": "2026-11-05T06:00:31Z", "event": "Task failed with exception", "level": "error",
         "error_detail": [{"exc_type": "ReadTimeout", "exc_value": "read timeout=30", "frames": [{"filename": "/dags/x.py", "lineno": 7, "name": "extract"}]}]},
    ]  # fmt: skip
    path = f"/api/v2/dags/sales_etl/dagRuns/{RUN}/taskInstances/extract_orders/logs/1"
    server = FakeServer().on("GET", path, (200, {"content": content, "continuation_token": None}))
    text = client(server).get_task_log("sales_etl", RUN, "extract_orders", 1)
    assert "INFO - Starting attempt 1 of 1" in text
    assert 'File "/dags/x.py", line 7, in extract' in text
    assert text.strip().endswith("ReadTimeout: read timeout=30")
    assert server.requests[0].url.params["full_content"] == "true"


def test_text_logs_and_continuation_tokens():
    path = f"/api/v2/dags/d/dagRuns/{RUN}/taskInstances/t/logs/2"
    server = FakeServer().on("GET", path, (200, {"content": "part one", "continuation_token": "tok"}), (200, {"content": "part two", "continuation_token": None}))
    assert client(server).get_task_log("d", RUN, "t", 2) == "part one\npart two"
    assert server.requests[1].url.params["token"] == "tok"


def test_dag_source_and_import_errors():  # FR-022, FR-007
    server = (
        FakeServer()
        .on("GET", "/api/v2/dagSources/sales_etl", (200, {"content": "from airflow import DAG", "dag_id": "sales_etl", "version_number": 3}))
        .on("GET", "/api/v2/importErrors", (200, {"import_errors": [{"import_error_id": 4, "timestamp": "2026-11-05T05:50:00Z", "filename": "/dags/broken.py", "bundle_name": "dags-folder", "stack_trace": "SyntaxError"}], "total_entries": 1}))
    )  # fmt: skip
    c = client(server)
    assert c.get_dag_source("sales_etl") == "from airflow import DAG"
    errors = c.list_import_errors()
    assert errors[0].filename == "/dags/broken.py" and errors[0].stack_trace == "SyntaxError"


# --------------------------------------------------------------------------- writes


def test_clear_task_instances_sends_a_run_scoped_failed_only_body():
    server = FakeServer().on("POST", "/api/v2/dags/sales_etl/clearTaskInstances", (200, {"task_instances": [ti("extract_orders"), ti("load_orders", "upstream_failed")], "total_entries": 2}))
    result = client(server).clear_task_instances("sales_etl", RUN, ["extract_orders"], dry_run=True, include_downstream=True)
    body = server.body(server.requests[0])
    assert body["dry_run"] is True and body["dag_run_id"] == RUN and body["task_ids"] == ["extract_orders"]
    assert body["only_failed"] is True and body["include_downstream"] is True
    assert body["include_upstream"] is False and body["include_past"] is False and body["include_future"] is False
    assert [t.state for t in result] == ["failed", "upstream_failed"]


def test_real_clear_is_never_repeated_on_server_error():  # FR-065 at the HTTP layer
    server = FakeServer().on("POST", "/api/v2/dags/sales_etl/clearTaskInstances", (503, {"detail": "busy"}))
    with pytest.raises(AirflowError) as info:
        client(server).clear_task_instances("sales_etl", RUN, ["extract_orders"], dry_run=False)
    assert info.value.retriable and len(server.requests) == 1


def test_dry_run_clear_may_be_retried():
    server = FakeServer().on("POST", "/api/v2/dags/d/clearTaskInstances", (503, {}), (200, {"task_instances": [], "total_entries": 0}))
    assert client(server).clear_task_instances("d", RUN, ["t"], dry_run=True) == []
    assert len(server.requests) == 2


def test_clear_requires_task_ids():
    with pytest.raises(AirflowError):
        client(FakeServer()).clear_task_instances("d", RUN, [], dry_run=True)


def test_set_state_uses_dry_run_endpoint():
    path = f"/api/v2/dags/d/dagRuns/{RUN}/taskInstances/t/dry_run"
    server = FakeServer().on("PATCH", path, (200, {"task_instances": [ti("t")], "total_entries": 1}))
    client(server).set_task_instance_state("d", RUN, "t", "success", dry_run=True)
    assert server.body(server.requests[0])["new_state"] == "success"


# --------------------------------------------------------------------------- outages (FR-012, NFR-001) and malformed data (NFR-010)


def test_transient_errors_on_reads_are_retried_then_succeed():
    server = FakeServer().on("GET", "/api/v2/dags/d", (503, {}), (502, {}), (200, {"dag_id": "d"}))
    assert client(server).get_dag("d").dag_id == "d"
    assert len(server.requests) == 3


@pytest.mark.parametrize("failure", [raise_timeout, raise_connect])
def test_airflow_unavailable_raises_retriable_error_after_bounded_retries(failure):
    server = FakeServer().on("GET", "/api/v2/dags/d", failure)
    with pytest.raises(AirflowError) as info:
        client(server, max_retries_on_transient_error=2).get_dag("d")
    assert info.value.retriable and len(server.requests) == 3


def test_not_found_is_not_retried():
    server = FakeServer().on("GET", "/api/v2/dags/missing", (404, {"detail": "DAG not found"}))
    with pytest.raises(AirflowError, match="DAG not found") as info:
        client(server).get_dag("missing")
    assert not info.value.retriable and len(server.requests) == 1


def test_429_honours_retry_after():
    sleeps: list[float] = []
    server = FakeServer().on("GET", "/api/v2/dags/d", httpx.Response(429, headers={"Retry-After": "3"}), (200, {"dag_id": "d"}))
    import oncall_agent.integrations.http as http_mod

    http_mod.time.sleep = sleeps.append  # restored by the autouse monkeypatch fixture
    client(server).get_dag("d")
    assert sleeps == [3.0]


@pytest.mark.parametrize(
    "reply",
    [
        (200, {"runs": []}),  # wrong key
        (200, {"dag_runs": [{"dag_run_id": "r1", "state": "failed"}], "total_entries": 1}),  # missing dag_id
        (200, {"dag_runs": [{"dag_id": "d", "dag_run_id": "r1", "state": "failed", "end_date": "not-a-date"}]}),
        (200, "<html>proxy error</html>"),  # not JSON
    ],
)
def test_malformed_responses_are_rejected(reply):
    server = FakeServer().on("GET", "/api/v2/dags/~/dagRuns", reply)
    with pytest.raises(MalformedResponseError):
        client(server).list_dag_runs()


def test_malformed_log_payload_is_rejected():
    server = FakeServer().on("GET", f"/api/v2/dags/d/dagRuns/{RUN}/taskInstances/t/logs/1", (200, {"content": 42}))
    with pytest.raises(MalformedResponseError):
        client(server).get_task_log("d", RUN, "t", 1)


def test_missing_base_url_is_rejected():
    with pytest.raises(AirflowError):
        HttpAirflowClient(AirflowSettings(token="x"))
