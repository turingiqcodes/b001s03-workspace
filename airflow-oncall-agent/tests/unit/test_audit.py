"""Audit service (NFR-025..029, NFR-009)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oncall_agent.logging_config import REDACTED, register_secrets
from oncall_agent.models import AuditEventType
from oncall_agent.services.audit import MAX_SUMMARY_CHARS, AuditService
from oncall_agent.store import Store

from ..conftest import make_incident

pytestmark = pytest.mark.unit


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_event_is_stored_in_database_and_json_lines(audit: AuditService, store: Store):
    inc = store.create_incident(make_incident())
    stored = audit.record(
        AuditEventType.POLICY_DECISION, actor="agent", action="evaluate_retry", incident_id=inc.id,
        input_summary={"classification": "retriable"}, output_summary="allowed", details={"reasons": []},
    )  # fmt: skip
    assert stored is not None and stored.id is not None
    assert audit.history(inc.id)[0].action == "evaluate_retry"
    line = read_jsonl(audit.jsonl_path)[-1]
    assert line["event_type"] == "policy_decision" and line["incident_id"] == inc.id and line["persisted"] is True
    assert json.loads(line["input_summary"]) == {"classification": "retriable"}


def test_secrets_are_redacted_before_storage(audit: AuditService):
    register_secrets(["audit-secret-value-1"])
    audit.record(
        AuditEventType.INTEGRATION_FAILURE, actor="agent", action="linear.create_issue",
        input_summary="key audit-secret-value-1", details={"headers": {"Authorization": "Bearer abcdefghijk"}},
    )  # fmt: skip
    text = audit.jsonl_path.read_text()
    db_event = audit.store.list_audit_events()[-1]
    for blob in (text, db_event.model_dump_json()):
        assert "audit-secret-value-1" not in blob and "abcdefghijk" not in blob
    assert REDACTED in text


def test_long_summaries_are_truncated(audit: AuditService):
    event = audit.record(AuditEventType.CONTEXT_COLLECTED, actor="agent", action="collect", output_summary="x" * 10_000)
    assert len(event.output_summary) == MAX_SUMMARY_CHARS and event.output_summary.endswith("[truncated]")


def test_store_failure_does_not_raise_and_is_still_logged_to_file(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite").open()
    service = AuditService(store, tmp_path / "audit.jsonl")
    store.close()  # every insert now fails
    assert service.record(AuditEventType.POLL_FAILED, actor="agent", action="poll") is None
    line = read_jsonl(tmp_path / "audit.jsonl")[-1]
    assert line["event_type"] == "poll_failed" and line["persisted"] is False


def test_invalid_event_does_not_raise(audit: AuditService):
    assert audit.record(AuditEventType.POLL_FAILED, actor="", action="poll") is None


def test_unwritable_jsonl_does_not_raise(store: Store, tmp_path: Path):
    target = tmp_path / "dir_instead_of_file"
    target.mkdir()
    service = AuditService(store, target)
    assert service.record(AuditEventType.POLL_COMPLETED, actor="agent", action="poll") is not None
