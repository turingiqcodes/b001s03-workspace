"""Code-change relationship heuristics (FR-027) and truncation helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oncall_agent.context import (
    CommitEvidence,
    FileEvidence,
    _head,
    _tail,
    error_identifiers,
    score_commit,
    traceback_files,
)

pytestmark = pytest.mark.unit

FAIL = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)
PATCH = '@@ -5 +5 @@\n-        "tier": r["tier"]\n+        "tier": r["customer_tier"]\n'


def commit(at: datetime, patch: str | None = PATCH, filename: str = "dags/customer_sync.py") -> CommitEvidence:
    return CommitEvidence(sha="a1b2c3d4e5", title="change", committed_at=at, files=[FileEvidence(filename=filename, status="modified", patch=patch)])


def score(c: CommitEvidence, *, identifiers=frozenset({"customer_tier"}), trace=frozenset({"/opt/airflow/dags/customer_sync.py"}), last_success=None) -> CommitEvidence:
    score_commit(c, failure_time=FAIL, identifiers=set(identifiers), trace_files=set(trace), last_success_start=last_success)
    return c


@pytest.mark.parametrize(
    "line,expected",
    [
        ("KeyError: 'customer_tier'", {"customer_tier"}),
        ("AttributeError: 'NoneType' object has no attribute 'load_orders'", {"NoneType", "load_orders"}),
        ("ValueError: bad value in rowCount", {"rowCount"}),
        ("KeyError: 'id'", set()),  # too short to be a useful signal
        ("TimeoutError at <addr>", set()),
        (None, set()),
    ],
)
def test_error_identifiers(line, expected):
    assert error_identifiers(line) == expected


def test_traceback_files_are_extracted():
    log = 'Traceback (most recent call last):\n  File "/opt/airflow/dags/x.py", line 7, in f\n  File "/usr/lib/python3/json.py", line 3, in g'
    assert traceback_files(log) == {"/opt/airflow/dags/x.py", "/usr/lib/python3/json.py"}
    assert traceback_files(None) == set()


def test_all_signals_add_up_and_are_explained():
    c = score(commit(FAIL - timedelta(hours=3)))
    assert c.relation_score == 6 and c.possibly_related
    assert any("'customer_tier'" in r for r in c.relation_reasons)
    assert any("traceback passes through" in r for r in c.relation_reasons)
    assert any("3.0h before the failure" in r for r in c.relation_reasons)


def test_identifier_only_in_context_lines_does_not_count():
    patch = '@@ -1,3 +1,3 @@\n  x = r["customer_tier"]\n-y = 1\n+y = 2\n'
    c = score(commit(FAIL - timedelta(days=3), patch=patch), trace=frozenset())
    assert c.relation_score == 0 and not c.possibly_related


def test_success_after_the_commit_lowers_the_score():
    c = score(commit(FAIL - timedelta(days=2)), last_success=FAIL - timedelta(days=1))
    assert c.relation_score == 3  # +3 identifier +2 traceback -2 succeeded afterwards
    assert any("succeeded after this commit" in r for r in c.relation_reasons)


def test_commit_after_the_failure_cannot_be_the_cause():
    c = score(commit(FAIL + timedelta(minutes=5)))
    assert c.relation_score == 0 and "after the failure" in c.relation_reasons[0]


def test_missing_patch_gives_no_patch_signal():
    c = score(commit(FAIL - timedelta(days=3), patch=None), trace=frozenset())
    assert c.relation_score == 0
    assert c.relation_reasons == ["touches the DAG file; no specific link to the error found"]


def test_score_never_negative():
    c = score(commit(FAIL - timedelta(days=3), patch=None), trace=frozenset(), last_success=FAIL - timedelta(days=1))
    assert c.relation_score == 0


def test_truncation_keeps_the_end_of_logs_and_the_start_of_source():
    text = "a" * 50 + "ERROR"
    tail, truncated = _tail(text, 10)
    assert truncated and tail.endswith("aaaaaERROR")
    head, truncated = _head(text, 10)
    assert truncated and head.startswith("aaaaaaaaaa")
    assert _tail("short", 10) == ("short", False)
