"""Domain model validation (FR-008, FR-034..FR-039, FR-044, FR-046, FR-066/067)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from oncall_agent.models import (
    AIAnalysisPayload,
    AnalysisResult,
    AnalysisSource,
    ApprovalRecord,
    AuditEvent,
    AuditEventType,
    Classification,
    FailureEvent,
    FailureType,
    Incident,
    IncidentStatus,
    ProposedAction,
)

pytestmark = pytest.mark.unit

VALID = {
    "classification": "retriable",
    "confidence": 0.85,
    "root_cause": "Upstream API returned 503 during extract",
    "evidence": ["log: HTTPError 503 Service Unavailable", "previous 9 runs succeeded"],
    "suggested_fix": "Retry the task; add retries with backoff to the operator",
    "proposed_action": "retry_task",
    "suspected_commit": None,
    "explanation": "Transient upstream outage; no related code change.",
}


# --------------------------------------------------------------------------- AI payload schema


def test_valid_payload_parses_from_json():  # AT-013
    p = AIAnalysisPayload.model_validate_json(json.dumps(VALID))
    assert p.classification is Classification.RETRIABLE
    assert p.proposed_action is ProposedAction.RETRY_TASK


@pytest.mark.parametrize(
    "change",
    [
        {"classification": "probably_fine"},  # FR-036
        {"proposed_action": "delete_dag"},  # FR-037
        {"confidence": 1.2},
        {"confidence": -0.1},
        {"confidence": "0.9"},  # string, not a number
        {"confidence": True},
        {"root_cause": ""},
        {"root_cause": "   "},
        {"evidence": []},
        {"evidence": ["ok", ""]},
        {"suggested_fix": ""},
        {"explanation": ""},
        {"suspected_commit": "not-a-sha"},
        {"unexpected_field": "x"},
    ],
)
def test_invalid_payloads_are_rejected(change):  # AT-014 / FR-038 / FR-039
    with pytest.raises(ValidationError):
        AIAnalysisPayload.model_validate_json(json.dumps({**VALID, **change}))


@pytest.mark.parametrize("missing", list(VALID))
def test_incomplete_payloads_are_rejected(missing):
    data = dict(VALID)
    data.pop(missing)
    if missing == "suspected_commit":  # optional: may be omitted
        AIAnalysisPayload.model_validate(data)
        return
    with pytest.raises(ValidationError):
        AIAnalysisPayload.model_validate(data)


def test_integer_confidence_and_valid_commit_are_accepted():
    p = AIAnalysisPayload.model_validate({**VALID, "confidence": 1, "suspected_commit": "a1b2c3d"})
    assert p.confidence == 1.0 and p.suspected_commit == "a1b2c3d"


def test_payload_json_schema_lists_all_required_fields():  # FR-035
    schema = AIAnalysisPayload.model_json_schema()
    assert set(schema["required"]) == set(VALID) - {"suspected_commit"}
    assert schema["additionalProperties"] is False


# --------------------------------------------------------------------------- analysis result


def test_ai_result_records_source():  # FR-046
    r = AnalysisResult.from_payload(AIAnalysisPayload.model_validate(VALID), source=AnalysisSource.AI, provider="groq", model="m")
    assert r.source is AnalysisSource.AI and r.provider == "groq" and r.confidence == 0.85


def test_fallback_confidence_above_cap_is_rejected():  # FR-044 / AT-016
    with pytest.raises(ValidationError, match="FR-044"):
        AnalysisResult.from_payload(AIAnalysisPayload.model_validate(VALID), source=AnalysisSource.FALLBACK, provider="rules")


def test_fallback_confidence_at_cap_is_accepted():
    payload = AIAnalysisPayload.model_validate({**VALID, "confidence": 0.5})
    r = AnalysisResult.from_payload(payload, source=AnalysisSource.FALLBACK, provider="rules", fallback_reason="timeout")
    assert r.confidence == 0.5 and r.fallback_reason == "timeout"


# --------------------------------------------------------------------------- failure events


def test_task_failure_event_identity():  # FR-008
    e = FailureEvent(failure_type=FailureType.TASK_FAILURE, dag_id="etl", dag_run_id="r1", task_id="load", try_number=2, state="failed")
    assert e.fingerprint_task_id == "load"
    assert e.event_key == "task:etl:r1:load:-1:2"


def test_dag_run_and_import_error_use_placeholder_task_ids():
    run = FailureEvent(failure_type=FailureType.DAG_RUN_FAILURE, dag_id="etl", dag_run_id="r1", state="failed")
    imp = FailureEvent(failure_type=FailureType.IMPORT_ERROR, dag_id="dags/etl.py", filename="dags/etl.py", state="import_error")
    assert run.fingerprint_task_id == "__dag_run__"
    assert imp.fingerprint_task_id == "__import_error__"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"failure_type": "task_failure", "dag_id": "etl", "dag_run_id": "r1"},  # no task_id
        {"failure_type": "dag_run_failure", "dag_id": "etl"},  # no run id
        {"failure_type": "import_error", "dag_id": "x"},  # no filename
        {"failure_type": "task_failure", "dag_id": "", "dag_run_id": "r", "task_id": "t"},
    ],
)
def test_malformed_failure_events_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        FailureEvent(state="failed", **kwargs)


# --------------------------------------------------------------------------- incidents, approvals, audit


def test_incident_requires_sha256_fingerprint():
    with pytest.raises(ValidationError):
        Incident(fingerprint="abc", fingerprint_input={}, failure_type=FailureType.TASK_FAILURE, dag_id="d", normalized_error="e")
    inc = Incident(fingerprint="a" * 64, fingerprint_input={"dag_id": "d"}, failure_type="task_failure", dag_id="d", normalized_error="e")
    assert inc.status is IncidentStatus.OPEN and inc.is_active and inc.retry_count == 0 and inc.cycle_retry_count == 0


@pytest.mark.parametrize("action", ["rerun_dag", "mark_success"])
def test_approval_can_be_requested_for_restricted_actions(action):  # FR-066/067
    assert ApprovalRecord(incident_id=1, requested_action=action, dag_id="d", dag_run_id="r").status.value == "pending"


@pytest.mark.parametrize("action", ["retry_task", "manual_investigation", "no_action"])
def test_approval_cannot_be_requested_for_other_actions(action):  # decision A2
    with pytest.raises(ValidationError):
        ApprovalRecord(incident_id=1, requested_action=action, dag_id="d", dag_run_id="r")


def test_audit_event_requires_actor_and_action():
    AuditEvent(event_type=AuditEventType.POLICY_DECISION, actor="agent", action="evaluate_retry")
    with pytest.raises(ValidationError):
        AuditEvent(event_type=AuditEventType.POLICY_DECISION, actor="", action="x")
