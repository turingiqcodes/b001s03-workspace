"""Ownership resolution (FR-078) and report rendering (FR-076/077, FR-029, NFR-009)."""
from __future__ import annotations

import pytest

from oncall_agent.exceptions import LinearError
from oncall_agent.integrations.mocks import MockLinearClient
from oncall_agent.logging_config import register_secrets
from oncall_agent.models import AnalysisSource, Classification, FailureType, IncidentStatus, ProposedAction
from oncall_agent.ownership import OwnershipResolver
from oncall_agent.reporting import (
    diagnostics_comment,
    fingerprint_marker,
    issue_description,
    issue_title,
    occurrence_comment,
    status_comment,
)

from ..conftest import make_incident

pytestmark = pytest.mark.unit

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
COMMIT = {"sha": SHA, "title": "Rename tier", "author_login": "alice-gh", "author_email": "alice@example.com", "author_name": "Alice",
          "possibly_related": True, "relation_score": 6, "relation_reasons": ["patch changes 'customer_tier'"]}  # fmt: skip


def incident(**kw):
    data = dict(context_summary={"dag_owners": ["data-eng"], "commits": [COMMIT]}, suspected_commit=SHA)
    data.update(kw)
    return make_incident(**data)


def resolver(user_map=None, default=None, linear=None):
    return OwnershipResolver(linear or MockLinearClient(), user_map or {}, default)


# --------------------------------------------------------------------------- ownership order (FR-078)


def test_commit_author_comes_first():
    o = resolver().resolve(incident())  # alice@example.com is a Linear user
    assert (o.source, o.assignee_id, o.who) == ("commit_author", "user-alice", "alice@example.com")


def test_commit_author_via_user_map():
    o = resolver({"alice-gh": "user-bob"}).resolve(incident())
    assert (o.source, o.assignee_id, o.who) == ("commit_author", "user-bob", "alice-gh")


def test_possibly_related_commit_is_used_without_a_suspected_commit():
    assert resolver().resolve(incident(suspected_commit=None)).source == "commit_author"


def test_dag_owner_is_second():
    summary = {"dag_owners": ["airflow", "data-eng"], "commits": [{**COMMIT, "author_email": "ghost@example.com", "author_login": "ghost"}]}
    o = resolver({"data-eng": "bob@example.com"}).resolve(incident(context_summary=summary))
    assert (o.source, o.assignee_id) == ("dag_owner", "user-bob")
    assert any("ghost@example.com: no Linear user" in n for n in o.notes)


def test_default_assignee_is_third():
    o = resolver(default="bob@example.com").resolve(incident(context_summary={"dag_owners": ["airflow"]}, suspected_commit=None))
    assert (o.source, o.assignee_id) == ("default_assignee", "user-bob")


def test_unassigned_with_recorded_reason():
    o = resolver().resolve(incident(context_summary={}, suspected_commit=None))
    assert o.assignee_id is None and o.source == "none"
    assert "left unassigned" in o.note and "default assignee: not configured" in o.note


def test_linear_lookup_failure_moves_on():
    linear = MockLinearClient()
    linear.fail_next("find_user", LinearError("timeout", retriable=True))
    o = resolver(default="bob@example.com", linear=linear).resolve(incident())
    assert o.assignee_id is not None and any("Linear lookup failed" in n for n in o.notes)


# --------------------------------------------------------------------------- reports


def analysed(**kw):
    data = dict(classification=Classification.NON_RETRIABLE, confidence=0.9, proposed_action=ProposedAction.MANUAL_INVESTIGATION,
                analysis_source=AnalysisSource.AI, root_cause="Field renamed", suggested_fix="Revert", evidence=["KeyError: 'customer_tier'"],
                missing_evidence=["dag_source: HTTP 503"], analysis_explanation="Commit changed the field.", status=IncidentStatus.ESCALATED,
                status_reason="a human must investigate",
                context_summary={"history": "9 of the last 10 runs succeeded", "commits": [COMMIT], "commit_search": "commits to dags/x.py", "log_tail": "KeyError: 'customer_tier'"})  # fmt: skip
    data.update(kw)
    return make_incident(**data)


def test_title_is_short_and_specific():
    t = issue_title(analysed(normalized_error="x" * 500))
    assert t.startswith("[oncall] sales_etl.extract_orders failed: ") and len(t) <= 120
    assert issue_title(analysed(failure_type=FailureType.IMPORT_ERROR, task_id=None, dag_id="dags/broken.py")).startswith("[oncall] import error in dags/broken.py")


def test_description_has_failure_analysis_and_fingerprint_marker():
    inc = analysed(suspected_commit=SHA)
    d = issue_description(inc, approval_label="oncall:approved", approve_command="/approve", ownership_note="assigned to author")
    assert "## Failure" in d and "## Analysis" in d and "non_retriable (confidence 0.90, AI analysis)" in d
    assert "`a1b2c3d` (not confirmed)" in d
    assert fingerprint_marker(inc.fingerprint) in d and "+++ Agent notes" in d
    assert "Approval needed" not in d


def test_description_explains_how_to_approve():
    d = issue_description(analysed(status=IncidentStatus.AWAITING_APPROVAL, proposed_action=ProposedAction.RERUN_DAG), approval_label="oncall:approved", approve_command="/approve")
    assert "## Approval needed" in d and "`oncall:approved`" in d and "`/approve`" in d and "trusted approver" in d


def test_diagnostics_keep_missing_evidence_separate():  # FR-029 / FR-077
    c = diagnostics_comment(analysed())
    evidence, missing = c.split("Missing evidence")
    assert "KeyError" in evidence and "dag_source: HTTP 503" not in evidence
    assert "not proof of anything" in missing and "dag_source: HTTP 503" in missing
    assert "Possibly related changes (heuristic, not confirmed)" in c and "9 of the last 10" in c


def test_no_commit_found_is_not_presented_as_proof():
    c = diagnostics_comment(analysed(context_summary={"commit_search": "commits to dags/x.py", "commits": []}))
    assert "not proof that nothing changed" in c


def test_fallback_is_labelled():
    d = issue_description(analysed(analysis_source=AnalysisSource.FALLBACK, confidence=0.45, analysis_fallback_reason="AI provider unavailable: timeout"),
                          approval_label="l", approve_command="/approve")  # fmt: skip
    assert "Rule-based fallback (AI unavailable)" in d and "AI provider unavailable: timeout" in d


def test_reports_are_redacted():  # NFR-009
    register_secrets(["super-secret-token-123"])
    inc = analysed(original_error="auth failed with super-secret-token-123", context_summary={"log_tail": "Authorization: Bearer abcdefghijkl"})
    text = issue_description(inc, approval_label="l", approve_command="/a") + diagnostics_comment(inc)
    assert "super-secret-token-123" not in text and "abcdefghijkl" not in text


def test_status_and_occurrence_comments():
    assert status_comment(analysed()) == "**Escalated to a human.** a human must investigate"
    assert "3 occurrences" in occurrence_comment(analysed(occurrence_count=3))
