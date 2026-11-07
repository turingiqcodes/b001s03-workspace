"""Rule-based fallback (FR-042..046; AT-016, AT-017 at the analysis level)."""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone

import pytest

from oncall_agent.context import CommitEvidence, InvestigationContext, MissingEvidence
from oncall_agent.models import FALLBACK_MAX_CONFIDENCE, AnalysisSource, Classification, FailureType, ProposedAction
from oncall_agent.providers.fallback import RULES, FallbackAnalyzer

pytestmark = pytest.mark.unit


def ctx(error: str | None, failure_type=FailureType.TASK_FAILURE, **kw) -> InvestigationContext:
    return InvestigationContext(incident_id=1, failure_type=failure_type, dag_id="d", task_id="t", dag_run_id="r",
                                original_error=error, normalized_error=error or "<no log available>", **kw)  # fmt: skip


@pytest.mark.parametrize(
    "error,classification,rule",
    [
        ("requests.exceptions.ReadTimeout: Read timed out. (read timeout=30)", Classification.RETRIABLE, "transient_network"),
        ("HTTPError: 503 Server Error: Service Unavailable", Classification.RETRIABLE, "upstream_unavailable"),
        ("psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout", Classification.RETRIABLE, "db_lock_or_deadlock"),
        ("KeyError: 'customer_tier'", Classification.NON_RETRIABLE, "code_error"),
        ("SyntaxError: invalid syntax", Classification.NON_RETRIABLE, "import_or_syntax_error"),
        ("ModuleNotFoundError: No module named 'pandas'", Classification.NON_RETRIABLE, "import_or_syntax_error"),
        ("botocore.exceptions.ClientError: AccessDenied", Classification.NON_RETRIABLE, "auth_or_permission"),
        ("Task exited with return code -9", Classification.UNCERTAIN, "killed_or_oom"),
        ("airflow.exceptions.AirflowTaskTimeout: Timeout, PID: 12", Classification.UNCERTAIN, "task_timeout"),
        ("FileNotFoundError: [Errno 2] No such file or directory: 'x.csv'", Classification.UNCERTAIN, "missing_input"),
        ("Something nobody has seen before", Classification.UNCERTAIN, "unknown"),
    ],
)
def test_known_patterns_are_identified(error, classification, rule):  # FR-043
    result = FallbackAnalyzer().analyze(ctx(error), "AI provider unavailable: timeout")
    assert result.classification is classification
    assert result.evidence[0] == f"fallback rule '{rule}' matched"
    assert result.source is AnalysisSource.FALLBACK and result.provider == "rules"  # FR-046
    assert result.fallback_reason == "AI provider unavailable: timeout"


def test_pattern_found_in_log_when_error_line_is_generic():
    c = ctx("Task failed", task_log="INFO start\nrequests.exceptions.ConnectionError: refused\nERROR - Task failed")
    assert FallbackAnalyzer().analyze(c, "x").classification is Classification.RETRIABLE


def test_dag_run_failure_is_uncertain():
    result = FallbackAnalyzer().analyze(ctx("DAG run ended in state 'failed'", failure_type=FailureType.DAG_RUN_FAILURE), "x")
    assert result.classification is Classification.UNCERTAIN and result.evidence[0] == "fallback rule 'dag_run_failure' matched"


def test_every_rule_respects_the_confidence_cap():  # FR-044
    assert all(rule.confidence <= FALLBACK_MAX_CONFIDENCE for rule in RULES)


def test_fallback_never_proposes_an_executable_action_for_any_input():  # FR-045 / AT-017 (analysis side)
    rng = random.Random(42)
    samples = ["ReadTimeout", "KeyError: 'a'", "503", "LockNotAvailable", "return code -9", ""]
    samples += ["".join(rng.choice(string.printable) for _ in range(rng.randint(0, 200))) for _ in range(200)]
    for text in samples:
        result = FallbackAnalyzer().analyze(ctx(text), "x")
        assert result.confidence <= FALLBACK_MAX_CONFIDENCE
        assert result.proposed_action is ProposedAction.MANUAL_INVESTIGATION
        assert "cannot authorize automatic retries" in result.explanation


def test_suspected_commit_only_for_code_errors_with_a_possible_link():
    related = CommitEvidence(sha="a" * 40, title="x", committed_at=datetime.now(timezone.utc), relation_score=5)
    code = FallbackAnalyzer().analyze(ctx("KeyError: 'customer_tier'", commits=[related]), "x")
    network = FallbackAnalyzer().analyze(ctx("ReadTimeout", commits=[related]), "x")
    assert code.suspected_commit == "a" * 40 and any("heuristic, not confirmed" in e for e in code.evidence)
    assert network.suspected_commit is None


def test_missing_evidence_is_listed_separately():  # FR-029
    c = ctx("KeyError: 'x'", missing=[MissingEvidence(source="task_log", kind="unavailable", reason="503"),
                                       MissingEvidence(source="run_history", kind="not_applicable", reason="n/a")])  # fmt: skip
    result = FallbackAnalyzer().analyze(c, "x")
    assert result.missing_evidence == ["task_log: 503"]
    assert not any("503" in e for e in result.evidence)


def test_no_error_text_at_all():
    result = FallbackAnalyzer().analyze(ctx(None), "x")
    assert result.classification is Classification.UNCERTAIN and result.confidence == 0.2
