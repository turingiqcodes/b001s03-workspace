"""Deterministic policy engine (NFR-032; FR-045, FR-047..055, FR-066/067/073;
AT-017..AT-022; Part 10 rules; scenarios "Global retry limit set to zero",
"DAG marked as no-retry")."""
from __future__ import annotations

import itertools

import pytest

from oncall_agent.config import PolicySettings
from oncall_agent.models import AnalysisSource, Classification, FailureType, IncidentStatus, ProposedAction
from oncall_agent.policy import MIN_AUTO_RETRY_CONFIDENCE, Decision, PolicyEngine

from ..conftest import make_incident

pytestmark = pytest.mark.unit

SETTINGS = PolicySettings(max_auto_retries=2, global_max_retries_per_hour=10, no_retry_dags=("finance_ledger_close",))


def eligible(**overrides):
    data = dict(
        classification=Classification.RETRIABLE, confidence=0.85, proposed_action=ProposedAction.RETRY_TASK,
        analysis_source=AnalysisSource.AI, status=IncidentStatus.INVESTIGATING, cycle_retry_count=0,
    )  # fmt: skip
    data.update(overrides)
    return make_incident(**data)


def decide(incident, global_count=0, settings=SETTINGS):
    return PolicyEngine(settings).evaluate(incident, global_count)


# --------------------------------------------------------------------------- AT-018: fully eligible


def test_fully_eligible_retriable_failure_passes():  # AT-018
    d = decide(eligible())
    assert d.decision is Decision.AUTO_RETRY and d.auto_retry_allowed
    assert d.action is ProposedAction.RETRY_TASK
    assert len(d.checks) == 10 and all(c.passed for c in d.checks)


def test_confidence_threshold_is_inclusive_and_fixed():
    assert decide(eligible(confidence=0.7)).auto_retry_allowed
    assert not decide(eligible(confidence=0.6999)).auto_retry_allowed
    assert MIN_AUTO_RETRY_CONFIDENCE == 0.7
    assert "confidence" not in " ".join(PolicySettings.model_fields)  # not configurable


# --------------------------------------------------------------------------- each condition blocks on its own


@pytest.mark.parametrize(
    "overrides,global_count,settings,failed_check",
    [
        ({"failure_type": FailureType.DAG_RUN_FAILURE, "task_id": None}, 0, SETTINGS, "task_failure"),
        ({"failure_type": FailureType.IMPORT_ERROR, "task_id": None, "dag_run_id": None}, 0, SETTINGS, "task_failure"),
        ({"classification": Classification.NON_RETRIABLE}, 0, SETTINGS, "classification_retriable"),  # AT-019
        ({"classification": Classification.UNCERTAIN}, 0, SETTINGS, "classification_retriable"),  # AT-020
        ({"classification": None}, 0, SETTINGS, "classification_retriable"),
        ({"confidence": 0.69}, 0, SETTINGS, "confidence_threshold"),
        ({"confidence": None}, 0, SETTINGS, "confidence_threshold"),  # FR-054
        ({"proposed_action": ProposedAction.MANUAL_INVESTIGATION}, 0, SETTINGS, "proposed_retry_task"),
        ({"proposed_action": None}, 0, SETTINGS, "proposed_retry_task"),
        ({"cycle_retry_count": 2}, 0, SETTINGS, "incident_retry_limit"),  # AT-021
        ({"cycle_retry_count": 5}, 0, SETTINGS, "incident_retry_limit"),
        ({}, 10, SETTINGS, "global_retry_limit"),  # AT-022
        ({}, 11, SETTINGS, "global_retry_limit"),
        ({}, None, SETTINGS, "global_retry_limit"),  # unknown count fails closed
        ({"dag_id": "finance_ledger_close"}, 0, SETTINGS, "dag_not_no_retry"),  # no-retry DAG
        ({}, 0, PolicySettings(max_auto_retries=0), "auto_retries_enabled"),  # FR-051
        ({}, 0, PolicySettings(global_max_retries_per_hour=0), "auto_retries_enabled"),  # global limit zero
        ({"analysis_source": AnalysisSource.FALLBACK, "confidence": 0.5}, 0, SETTINGS, "analysis_from_ai"),  # AT-017
        ({"analysis_source": None}, 0, SETTINGS, "analysis_from_ai"),
        ({"status": IncidentStatus.RETRYING}, 0, SETTINGS, "incident_state"),
        ({"status": IncidentStatus.AWAITING_APPROVAL}, 0, SETTINGS, "incident_state"),
        ({"status": IncidentStatus.ESCALATED}, 0, SETTINGS, "incident_state"),
        ({"status": IncidentStatus.RESOLVED}, 0, SETTINGS, "incident_state"),
    ],
)
def test_each_condition_blocks_automatic_retry(overrides, global_count, settings, failed_check):
    d = decide(eligible(**overrides), global_count, settings)
    assert d.decision is not Decision.AUTO_RETRY and not d.auto_retry_allowed
    assert failed_check in {c.name for c in d.failed_checks}
    assert d.reasons and all(r for r in d.reasons)  # FR-053: always explained


def test_fallback_cannot_authorize_retry_even_with_high_confidence():  # FR-045 / AT-017
    # The model enforces ≤ 0.5 for fallback, but the policy does not rely on that.
    d = decide(eligible(analysis_source=AnalysisSource.FALLBACK))
    assert d.decision is Decision.ESCALATE
    assert any("fallback can never authorize" in r for r in d.reasons)


def test_policy_decision_is_fully_auditable():  # FR-053
    audit = decide(eligible(confidence=0.4)).to_audit()
    assert audit["decision"] == "escalate" and len(audit["checks"]) == 10
    failed = [c for c in audit["checks"] if not c["passed"]]
    assert failed == [{"name": "confidence_threshold", "passed": False, "detail": "confidence 0.4 (needs ≥ 0.7)"}]


def test_exhaustive_grid_auto_retry_only_when_every_condition_holds():  # Part 10, fail closed
    engine = PolicyEngine(SETTINGS)
    grid = itertools.product(
        [FailureType.TASK_FAILURE, FailureType.DAG_RUN_FAILURE],
        list(Classification) + [None],
        [None, 0.2, 0.69, 0.7, 1.0],
        list(ProposedAction) + [None],
        [0, 1, 2],
        [0, 9, 10, None],
        ["sales_etl", "finance_ledger_close"],
        list(AnalysisSource) + [None],
        list(IncidentStatus),
    )
    allowed = 0
    for ftype, cls, conf, action, cycle, glob, dag, source, status in grid:
        if source is AnalysisSource.FALLBACK and conf is not None and conf > 0.5:
            continue  # impossible state (model-level cap)
        inc = make_incident(
            failure_type=ftype, task_id="t" if ftype is FailureType.TASK_FAILURE else None, dag_id=dag, classification=cls,
            confidence=conf, proposed_action=action, cycle_retry_count=cycle, analysis_source=source, status=status,
        )  # fmt: skip
        d = engine.evaluate(inc, glob)
        expected = (
            ftype is FailureType.TASK_FAILURE and cls is Classification.RETRIABLE and conf is not None and conf >= 0.7
            and action is ProposedAction.RETRY_TASK and cycle < 2 and glob is not None and glob < 10
            and dag != "finance_ledger_close" and source is AnalysisSource.AI
            and status in (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)
        )  # fmt: skip
        assert d.auto_retry_allowed is expected, (ftype, cls, conf, action, cycle, glob, dag, source, status)
        allowed += expected
    # Allowed only for: confidence {0.7, 1.0} × cycle {0, 1} × global {0, 9} × status {open, investigating}
    assert allowed == 2 * 2 * 2 * 2
