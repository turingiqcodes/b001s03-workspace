"""Error normalization and fingerprints (FR-013..FR-016, FR-020; AT-009)."""
from __future__ import annotations

import hashlib

import pytest

from oncall_agent.fingerprint import (
    MAX_LINE_CHARS,
    NO_LOG,
    compute_fingerprint,
    extract_error_line,
    fingerprint_error,
    normalize_error,
)
from oncall_agent.integrations.mocks.sample_data import build_airflow_sample

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- error line extraction


def test_last_exception_line_wins_over_trailing_boilerplate():
    log = "\n".join([
        "[2026-11-05T06:00:01+00:00] INFO - Starting attempt 1 of 1",
        "Traceback (most recent call last):",
        '  File "/dags/x.py", line 7, in extract',
        "ValueError: first problem",
        "During handling of the above exception, another exception occurred:",
        "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api', port=443): Read timed out.",
        "[2026-11-05T06:00:31+00:00] ERROR - Task failed with exception",
        "[2026-11-05T06:00:31+00:00] INFO - Marking task as FAILED.",
    ])  # fmt: skip
    assert extract_error_line(log).startswith("requests.exceptions.ReadTimeout:")


@pytest.mark.parametrize(
    "line",
    [
        "KeyError: 'customer_tier'",
        "psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout",
        "airflow.exceptions.AirflowTaskTimeout: Timeout, PID: 1234",
        "SyntaxError: invalid syntax",
        "ConnectionRefusedError: [Errno 111] Connection refused",
        "botocore.exceptions.ClientError: An error occurred (AccessDenied)",
    ],
)
def test_exception_lines_are_recognised(line):
    assert extract_error_line(f"INFO - working\n{line}\nERROR - Task failed with exception") == line


def test_without_exception_the_last_meaningful_line_is_used():
    log = "INFO - Starting attempt 1 of 1\nINFO - Loading rows\nERROR - Task exited with return code -9"
    assert extract_error_line(log) == "Task exited with return code -9"


@pytest.mark.parametrize("text", [None, "", "   \n  ", "Traceback (most recent call last):\nERROR - Task failed with exception"])
def test_missing_or_useless_text_gives_no_log_marker(text):
    assert extract_error_line(text) == NO_LOG


@pytest.mark.parametrize(
    "prefixed",
    [
        "[2026-11-05T06:00:31.123+00:00] ERROR - KeyError: 'x'",
        "2026-11-05 06:00:31,123 ERROR - KeyError: 'x'",
        "[2026-11-05, 06:00:31 UTC] {taskinstance.py:441} ERROR - KeyError: 'x'",
        "ERROR - KeyError: 'x'",
    ],
)
def test_log_prefixes_are_stripped(prefixed):
    assert extract_error_line(prefixed) == "KeyError: 'x'"


# --------------------------------------------------------------------------- normalization (FR-013/014)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("failed at 2026-11-05T06:00:31.123456+00:00 retry", "failed at <ts> retry"),
        ("partition 2026-11-05 missing", "partition <date> missing"),
        ("object at 0x7f3a2c1e4b50 is gone", "object at <addr> is gone"),
        ("job 3f2b9c1e-4a5d-4e6f-8a7b-1c2d3e4f5a6b failed", "job <uuid> failed"),
        ("container 4f9a8b7c6d5e3f21a0b1 died", "container <hex> died"),
        ("run scheduled__2026-11-05T06:00:00+00:00 failed", "run <run_id> failed"),
        ("cannot open /tmp/tmpa1b2c3/data.csv: denied", "cannot open <tmp_path>: denied"),
        ("connect to 10.0.12.7:5432 refused", "connect to <ip> refused"),
        ("killed pid=48213 after", "killed pid=<n> after"),
        ("gave up after 12.5s waiting", "gave up after <duration> waiting"),
        ("loaded 48,211,903 rows", "loaded <n> rows"),
        ("row 1234567 is invalid", "row <n> is invalid"),
        ("  too    much\t whitespace ", "too much whitespace"),
    ],
)
def test_volatile_values_are_normalized(raw, expected):
    assert normalize_error(raw) == expected


@pytest.mark.parametrize(
    "stable",
    [
        "HTTPError: 503 Server Error",
        "Task exited with return code -9",
        "Read timed out. (read timeout=30)",
        "KeyError: 'customer_tier'",
        "HTTPError: 404 Client Error: Not Found for url: https://api.example.com/v2/orders",
    ],
)
def test_meaningful_values_are_kept(stable):
    assert normalize_error(stable) == stable


def test_normalized_line_is_truncated():
    assert len(normalize_error("ValueError: " + "x" * 5000)) == MAX_LINE_CHARS


# --------------------------------------------------------------------------- fingerprints (FR-015, AT-009)


def test_fingerprint_is_sha256_of_pipe_joined_inputs():  # FR-015
    expected = hashlib.sha256(b"sales_etl|extract_orders|KeyError: 'x'").hexdigest()
    assert compute_fingerprint("sales_etl", "extract_orders", "KeyError: 'x'") == expected


def test_equivalent_errors_produce_the_same_fingerprint():  # AT-009
    a = "[2026-11-05T06:00:31+00:00] ERROR - TimeoutError: worker 0x7f00aa11 run scheduled__2026-11-05T06:00:00+00:00 after 30.2s at 2026-11-05T06:00:31Z"
    b = "[2026-11-06T09:12:02+00:00] ERROR - TimeoutError: worker 0x7f99bb22 run scheduled__2026-11-06T06:00:00+00:00 after 31.9s at 2026-11-06T09:12:02Z"
    fa = fingerprint_error("sales_etl", "extract_orders", a, error_source="log")
    fb = fingerprint_error("sales_etl", "extract_orders", b, error_source="log")
    assert fa.value == fb.value
    assert fa.original_error_line != fb.original_error_line  # originals kept distinct (FR-019)


@pytest.mark.parametrize(
    "dag,task,text",
    [
        ("sales_etl", "extract_orders", "KeyError: 'other_field'"),  # different error
        ("sales_etl", "load_orders", "KeyError: 'customer_tier'"),  # different task
        ("customer_sync", "extract_orders", "KeyError: 'customer_tier'"),  # different DAG
        ("sales_etl", "extract_orders", "HTTPError: 404 Client Error"),
    ],
)
def test_different_failures_produce_different_fingerprints(dag, task, text):
    base = fingerprint_error("sales_etl", "extract_orders", "KeyError: 'customer_tier'", error_source="log")
    assert fingerprint_error(dag, task, text, error_source="log").value != base.value


def test_http_status_codes_are_not_merged():
    a = fingerprint_error("d", "t", "HTTPError: 503 Server Error", error_source="log")
    b = fingerprint_error("d", "t", "HTTPError: 500 Server Error", error_source="log")
    assert a.value != b.value


def test_fingerprint_inputs_are_recorded():  # FR-020
    fp = fingerprint_error("d", "t", "x\nKeyError: 'k' at 0xdeadbeef", error_source="log")
    assert fp.inputs() == {
        "algorithm": "sha256",
        "format": "dag_id|task_id|normalized_error_line",
        "dag_id": "d",
        "task_id": "t",
        "normalized_error_line": "KeyError: 'k' at <addr>",
        "original_error_line": "KeyError: 'k' at 0xdeadbeef",
        "error_source": "log",
    }


def test_missing_log_is_marked_as_such():  # FR-028/029
    fp = fingerprint_error("d", "t", None, error_source="log")
    assert fp.normalized_error_line == NO_LOG and fp.error_source == "none"


def test_sample_scenarios_extract_the_expected_lines():
    sample = build_airflow_sample()
    lines = {key[0]: extract_error_line(text) for key, text in sample.logs.items()}
    assert lines["sales_etl"].startswith("requests.exceptions.ReadTimeout")
    assert lines["customer_sync"] == "KeyError: 'customer_tier'"
    assert lines["ml_feature_build"] == "Task exited with return code -9"
    assert lines["finance_ledger_close"].startswith("psycopg2.errors.LockNotAvailable")
    assert extract_error_line(sample.import_errors[0].stack_trace) == "SyntaxError: invalid syntax"
