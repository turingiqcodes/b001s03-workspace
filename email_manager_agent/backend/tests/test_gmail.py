"""Gmail service: search, read, compose limits and write proposals."""
import pytest
from pydantic import ValidationError

from app.errors import BridgeError, ValidationFailed
from app.services import gmail_service as gmail

MESSAGE = {"id": "m1", "from": "alice@example.com", "subject": "Quarterly report", "date": "2026-11-09T07:00:00Z",
           "body": "Hello", "attachments": []}


def test_search_caps_max_results(ctx, bridge, settings):
    settings.email_search_max_results = 5
    bridge.on("gmail.search", {"messages": [{"id": "m1"}]})
    out = gmail.search_emails(ctx, gmail.SearchEmailsInput(query="from:alice@example.com", max_results=50))
    assert out["count"] == 1
    assert bridge.calls[0] == ("gmail.search", {"query": "from:alice@example.com", "max_results": 5})


def test_read_truncates_long_body(ctx, bridge, settings):
    settings.email_body_max_chars = 600
    bridge.on("gmail.read", {**MESSAGE, "body": "x" * 5000})
    out = gmail.read_email(ctx, gmail.ReadEmailInput(message_id="m1"))
    assert len(out["body"]) == 600 and out["body_truncated"] is True
    assert bridge.calls[0][1]["max_body_chars"] == 600


def test_read_bridge_error_propagates(ctx, bridge):
    bridge.on("gmail.read", BridgeError("Message not found", code="bridge_not_found"))
    with pytest.raises(BridgeError, match="not found"):
        gmail.read_email(ctx, gmail.ReadEmailInput(message_id="nope"))


@pytest.mark.parametrize("bad", [
    {"to": [], "subject": "s", "body": "b"},
    {"to": ["not-an-email"], "subject": "s", "body": "b"},
    {"to": ["a@example.com, b@example.com"], "subject": "s", "body": "b"},  # header injection attempt
    {"to": ["a@example.com"], "subject": "", "body": "b"},
    {"to": ["a@example.com"], "subject": "s", "body": "b", "html": "<b>"},
])
def test_compose_input_validation(bad):
    with pytest.raises(ValidationError):
        gmail.ComposeEmailInput.model_validate(bad)


def test_message_id_validation():
    with pytest.raises(ValidationError):
        gmail.ReadEmailInput(message_id="../../etc/passwd")


def test_send_limits(ctx, settings):
    settings.outgoing_body_max_chars = 500
    too_long = gmail.ComposeEmailInput(to=["a@example.com"], subject="s", body="x" * 501)
    with pytest.raises(ValidationFailed, match="email body"):
        gmail.propose_send_email(ctx, too_long)
    settings.max_recipients = 2
    crowd = gmail.ComposeEmailInput(to=["a@example.com", "b@example.com"], cc=["c@example.com"], subject="s", body="b")
    with pytest.raises(ValidationFailed, match="Too many recipients"):
        gmail.propose_send_email(ctx, crowd)


def test_send_and_draft_proposals(ctx, bridge):
    params = gmail.ComposeEmailInput(to=["bob@example.com"], subject="Lunch", body="Noon?")
    proposal = gmail.propose_send_email(ctx, params)
    assert proposal.summary == 'Send email to bob@example.com: "Lunch"'
    assert proposal.execute_params["body"] == "Noon?"
    assert gmail.propose_create_draft(ctx, params).summary.startswith("Save a draft")
    assert bridge.calls == []  # proposing never touches Gmail for new emails


def test_reply_proposal_shows_original(ctx, bridge):
    bridge.on("gmail.read", MESSAGE)
    proposal = gmail.propose_reply_email(ctx, gmail.ReplyEmailInput(message_id="m1", body="Thanks!"))
    assert proposal.summary == 'Reply to alice@example.com "Quarterly report"'
    assert proposal.execute_params == {"message_id": "m1", "body": "Thanks!", "reply_all": False}
    assert bridge.ops() == ["gmail.read"]

    draft = gmail.propose_create_reply_draft(ctx, gmail.ReplyEmailInput(message_id="m1", body="Thanks!", reply_all=True))
    assert draft.summary.startswith("Save a reply draft to everyone on")


def test_forward_proposal(ctx, bridge):
    bridge.on("gmail.read", MESSAGE)
    proposal = gmail.propose_forward_email(ctx, gmail.ForwardEmailInput(message_id="m1", to=["carol@example.com"], note="FYI"))
    assert "carol@example.com" in proposal.summary
    assert proposal.execute_params == {"message_id": "m1", "to": ["carol@example.com"], "note": "FYI"}


def test_label_changes_only_for_existing_labels(ctx, bridge):
    bridge.on("gmail.read", MESSAGE)
    bridge.on("gmail.listLabels", {"labels": ["Work", "Receipts"]})
    with pytest.raises(ValidationFailed, match="Unknown label"):
        gmail.propose_modify_labels(ctx, gmail.ModifyLabelsInput(message_id="m1", add_labels=["Nope"]))
    with pytest.raises(ValidationFailed, match="Nothing to change"):
        gmail.propose_modify_labels(ctx, gmail.ModifyLabelsInput(message_id="m1"))
    proposal = gmail.propose_modify_labels(ctx, gmail.ModifyLabelsInput(message_id="m1", add_labels=["Work"], mark_read=True))
    assert "add Work" in proposal.summary and "mark as read" in proposal.summary


def test_execute_functions_call_expected_operations(ctx, bridge):
    for op in ("gmail.send", "gmail.createDraft", "gmail.reply", "gmail.createReplyDraft", "gmail.forward", "gmail.modifyLabels"):
        bridge.on(op, {"ok": op})
    gmail.execute_send_email(ctx, {})
    gmail.execute_create_draft(ctx, {})
    gmail.execute_reply_email(ctx, {})
    gmail.execute_create_reply_draft(ctx, {})
    gmail.execute_forward_email(ctx, {})
    gmail.execute_modify_labels(ctx, {})
    assert bridge.ops() == ["gmail.send", "gmail.createDraft", "gmail.reply", "gmail.createReplyDraft", "gmail.forward", "gmail.modifyLabels"]
