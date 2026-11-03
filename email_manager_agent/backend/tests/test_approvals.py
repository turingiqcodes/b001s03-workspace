"""Approval rules: nothing is written without a matching, valid, single approval."""
from datetime import timedelta

import pytest

from app.agent.agent_loop import Agent
from app.approvals import AcknowledgementRequired, ApprovalExpired, decide
from app.errors import ApprovalError, BridgeTimeout, NotFound
from app.sessions import SessionStore

from tests.conftest import FakeClaude, response, text_block, tool_block

SEND = {"to": ["bob@example.com"], "subject": "Hi", "body": "Hello"}


@pytest.fixture
def store(now_fn):
    return SessionStore(3600, now_fn)


@pytest.fixture
def session(store):
    return store.get_or_create(None)


def _propose(ctx, session, tool="send_email", params=None):
    claude = FakeClaude([response(tool_block("t1", tool, params or SEND)), response(text_block("Please approve."))])
    result = Agent(claude, ctx).run(session, "do it")
    return result, claude


def test_write_tool_is_not_executed_by_the_agent(ctx, bridge, session):
    bridge.on("gmail.send", {"sent": True})
    result, claude = _propose(ctx, session)
    assert bridge.ops() == []
    assert result.tool_calls[0].status == "proposed"
    told_claude = claude.requests[1][-1]["content"][0]["content"]
    assert "awaiting_user_approval" in told_claude and "NOT executed" in told_claude
    assert len(result.proposed) == 1 and result.proposed[0].status == "pending"


def test_approved_operation_runs_once(ctx, bridge, session):
    bridge.on("gmail.send", {"sent": True})
    result, _ = _propose(ctx, session)
    op_id = result.proposed[0].id
    op = decide(session, op_id, "approve", False, ctx)
    assert op.status == "executed" and bridge.ops() == ["gmail.send"]
    with pytest.raises(ApprovalError, match="already executed"):
        decide(session, op_id, "approve", False, ctx)
    assert bridge.ops() == ["gmail.send"]  # no second send


def test_rejected_operation_is_never_executed(ctx, bridge, session):
    result, _ = _propose(ctx, session)
    op_id = result.proposed[0].id
    assert decide(session, op_id, "reject", False, ctx).status == "rejected"
    with pytest.raises(ApprovalError):
        decide(session, op_id, "approve", False, ctx)
    assert bridge.ops() == []


def test_unknown_operation_id(ctx, session):
    with pytest.raises(NotFound):
        decide(session, "op_doesnotexist1", "approve", False, ctx)


def test_operation_from_another_session_cannot_be_approved(ctx, bridge, store):
    first, second = store.get_or_create(None), store.get_or_create(None)
    result, _ = _propose(ctx, first)
    with pytest.raises(NotFound):
        decide(second, result.proposed[0].id, "approve", False, ctx)
    assert bridge.ops() == []


def test_expired_operation_is_refused(ctx, bridge, session, clock, settings):
    result, _ = _propose(ctx, session)
    clock["now"] += timedelta(seconds=settings.approval_ttl_seconds + 1)
    with pytest.raises(ApprovalExpired):
        decide(session, result.proposed[0].id, "approve", False, ctx)
    assert bridge.ops() == []
    assert any("expired" in note for note in session.pending_notes)


def test_warnings_need_acknowledgement(ctx, bridge, session):
    bridge.on("calendar.listEvents", {"events": []})
    bridge.on("calendar.createEvent", {"event_id": "e1"})
    # Saturday: outside working days -> warning
    params = {"title": "Sync", "start": "2026-11-14T10:00:00", "end": "2026-11-14T10:30:00"}
    result, _ = _propose(ctx, session, "create_event", params)
    op = result.proposed[0]
    assert op.warnings and op.public()["requires_acknowledgement"] is True
    with pytest.raises(AcknowledgementRequired):
        decide(session, op.id, "approve", False, ctx)
    assert "calendar.createEvent" not in bridge.ops()
    assert decide(session, op.id, "approve", True, ctx).status == "executed"


def test_identical_proposal_reuses_pending_operation(ctx, session):
    first, _ = _propose(ctx, session)
    second, _ = _propose(ctx, session)
    assert first.proposed[0].id == second.proposed[0].id
    assert len(session.open_operations()) == 1


def test_repeat_of_executed_operation_is_flagged(ctx, bridge, session):
    bridge.on("gmail.send", {"sent": True})
    first, _ = _propose(ctx, session)
    decide(session, first.proposed[0].id, "approve", False, ctx)
    second, _ = _propose(ctx, session)
    new_op = second.proposed[0]
    assert new_op.id != first.proposed[0].id
    assert any("already carried out" in w for w in new_op.warnings)


def test_open_operation_limit(ctx, session):
    for i in range(5):
        _propose(ctx, session, params={**SEND, "subject": f"Hi {i}"})
    result, claude = _propose(ctx, session, params={**SEND, "subject": "one too many"})
    assert result.tool_calls[0].status == "error"
    assert "already 5 operations" in claude.requests[1][-1]["content"][0]["content"]


def test_failed_execution_is_not_retryable(ctx, bridge, session):
    bridge.on("gmail.send", BridgeTimeout("Google did not answer"))
    result, _ = _propose(ctx, session)
    op = decide(session, result.proposed[0].id, "approve", False, ctx)
    assert op.status == "failed" and "may still have gone through" in op.error
    with pytest.raises(ApprovalError):
        decide(session, op.id, "approve", False, ctx)
    assert bridge.ops() == ["gmail.send"]


def test_frontend_cannot_change_what_runs(client, claude, bridge):
    """Extra fields in /confirm are rejected; the stored parameters are what execute."""
    bridge.on("gmail.send", {"sent": True})
    claude.script += [response(tool_block("t1", "send_email", SEND)), response(text_block("approve?"))]
    data = client.post("/api/prompt", json={"prompt": "send"}).json()
    op_id = data["pending_operations"][0]["id"]
    res = client.post("/api/confirm", json={
        "session_id": data["session_id"], "operation_id": op_id, "decision": "approve",
        "execute_params": {"to": ["attacker@example.com"]},
    })
    assert res.status_code == 422 and bridge.ops() == []
    client.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op_id, "decision": "approve"})
    assert bridge.calls[-1][1]["to"] == ["bob@example.com"]


def test_duplicate_confirm_over_http(client, claude, bridge):
    bridge.on("gmail.send", {"sent": True})
    claude.script += [response(tool_block("t1", "send_email", SEND)), response(text_block("approve?"))]
    data = client.post("/api/prompt", json={"prompt": "send"}).json()
    body = {"session_id": data["session_id"], "operation_id": data["pending_operations"][0]["id"], "decision": "approve"}
    assert client.post("/api/confirm", json=body).status_code == 200
    second = client.post("/api/confirm", json=body)
    assert second.status_code == 409 and second.json()["error"]["code"] == "approval_error"
    assert bridge.ops().count("gmail.send") == 1


def test_expired_confirm_over_http(client, claude, clock, settings):
    claude.script += [response(tool_block("t1", "send_email", SEND)), response(text_block("approve?"))]
    data = client.post("/api/prompt", json={"prompt": "send"}).json()
    clock["now"] += timedelta(seconds=settings.approval_ttl_seconds + 5)
    res = client.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": data["pending_operations"][0]["id"], "decision": "approve"})
    assert res.status_code == 410 and res.json()["error"]["code"] == "approval_expired"
