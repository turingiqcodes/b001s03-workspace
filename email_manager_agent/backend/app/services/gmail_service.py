"""Gmail tools: read operations run straight away, write operations become proposals."""
from typing import Any

from pydantic import Field

from app.errors import ValidationFailed
from app.services.common import (
    EmailAddress,
    Proposal,
    ResourceId,
    ServiceContext,
    ToolInput,
    check_recipient_count,
    check_text_limit,
    require_dict,
    require_list,
)


# ---------- Inputs ----------

class SearchEmailsInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="Gmail search syntax, e.g. 'is:unread newer_than:1d' or 'from:alice@example.com'.",
    )
    max_results: int = Field(10, ge=1, le=50, description="How many messages to return.")


class ReadEmailInput(ToolInput):
    message_id: ResourceId = Field(description="Message id from search_emails.")


class ComposeEmailInput(ToolInput):
    to: list[EmailAddress] = Field(min_length=1, description="Recipient email addresses.")
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1, description="Plain-text body.")


class ReplyEmailInput(ToolInput):
    message_id: ResourceId = Field(description="Message id of the email being answered.")
    body: str = Field(min_length=1, description="Plain-text reply body.")
    reply_all: bool = Field(False, description="Reply to all recipients instead of only the sender.")


class ForwardEmailInput(ToolInput):
    message_id: ResourceId
    to: list[EmailAddress] = Field(min_length=1)
    note: str = Field("", description="Optional text added above the forwarded message.")


class ModifyLabelsInput(ToolInput):
    message_id: ResourceId
    add_labels: list[str] = Field(default_factory=list, description="Existing user label names to add.")
    remove_labels: list[str] = Field(default_factory=list, description="User label names to remove.")
    mark_read: bool | None = Field(None, description="true = mark read, false = mark unread, omit = leave.")


class NoInput(ToolInput):
    pass


# ---------- Helpers ----------

def _check_compose_limits(ctx: ServiceContext, params: ComposeEmailInput) -> None:
    settings = ctx.settings
    check_recipient_count(settings, params.to, params.cc, params.bcc)
    check_text_limit(params.subject, settings.subject_max_chars, "subject")
    check_text_limit(params.body, settings.outgoing_body_max_chars, "email body")


def _message_header(ctx: ServiceContext, message_id: str) -> dict[str, Any]:
    """Fetch a short header for an existing message, to show the user what is being answered."""
    message = require_dict(
        ctx.bridge.call("gmail.read", {"message_id": message_id, "max_body_chars": 300}), "gmail.read"
    )
    return {
        "message_id": message_id,
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
    }


# ---------- Read tools ----------

def search_emails(ctx: ServiceContext, params: SearchEmailsInput) -> dict[str, Any]:
    max_results = min(params.max_results, ctx.settings.email_search_max_results)
    data = require_dict(
        ctx.bridge.call("gmail.search", {"query": params.query, "max_results": max_results}), "gmail.search"
    )
    messages = require_list(data.get("messages", []), "gmail.search")
    return {"query": params.query, "count": len(messages), "messages": messages}


def read_email(ctx: ServiceContext, params: ReadEmailInput) -> dict[str, Any]:
    limit = ctx.settings.email_body_max_chars
    message = require_dict(
        ctx.bridge.call("gmail.read", {"message_id": params.message_id, "max_body_chars": limit}), "gmail.read"
    )
    body = str(message.get("body", ""))
    # The bridge already truncates; this makes the limit hold even if it does not.
    if len(body) > limit:
        message["body"] = body[:limit]
        message["body_truncated"] = True
    return message


def list_labels(ctx: ServiceContext, params: NoInput) -> dict[str, Any]:
    data = require_dict(ctx.bridge.call("gmail.listLabels", {}), "gmail.listLabels")
    return {"labels": require_list(data.get("labels", []), "gmail.listLabels")}


# ---------- Write tools: propose ----------

def _compose_details(params: ComposeEmailInput) -> dict[str, Any]:
    return {
        "to": params.to, "cc": params.cc, "bcc": params.bcc,
        "subject": params.subject, "body": params.body,
    }


def propose_send_email(ctx: ServiceContext, params: ComposeEmailInput) -> Proposal:
    _check_compose_limits(ctx, params)
    details = _compose_details(params)
    return Proposal(
        summary=f"Send email to {', '.join(params.to)}: \"{params.subject}\"",
        details=details,
        execute_params=details,
    )


def propose_create_draft(ctx: ServiceContext, params: ComposeEmailInput) -> Proposal:
    _check_compose_limits(ctx, params)
    details = _compose_details(params)
    return Proposal(
        summary=f"Save a draft to {', '.join(params.to)}: \"{params.subject}\"",
        details=details,
        execute_params=details,
    )


def _propose_reply(ctx: ServiceContext, params: ReplyEmailInput, *, draft: bool) -> Proposal:
    check_text_limit(params.body, ctx.settings.outgoing_body_max_chars, "reply body")
    original = _message_header(ctx, params.message_id)
    who = "everyone on" if params.reply_all else original["from"] or "the sender of"
    action = "Save a reply draft to" if draft else "Reply to"
    return Proposal(
        summary=f"{action} {who} \"{original['subject']}\"",
        details={"in_reply_to": original, "reply_all": params.reply_all, "body": params.body},
        execute_params={"message_id": params.message_id, "body": params.body, "reply_all": params.reply_all},
    )


def propose_reply_email(ctx: ServiceContext, params: ReplyEmailInput) -> Proposal:
    return _propose_reply(ctx, params, draft=False)


def propose_create_reply_draft(ctx: ServiceContext, params: ReplyEmailInput) -> Proposal:
    return _propose_reply(ctx, params, draft=True)


def propose_forward_email(ctx: ServiceContext, params: ForwardEmailInput) -> Proposal:
    check_recipient_count(ctx.settings, params.to)
    check_text_limit(params.note, ctx.settings.outgoing_body_max_chars, "forward note")
    original = _message_header(ctx, params.message_id)
    return Proposal(
        summary=f"Forward \"{original['subject']}\" to {', '.join(params.to)}",
        details={"original": original, "to": params.to, "note": params.note},
        execute_params={"message_id": params.message_id, "to": params.to, "note": params.note},
    )


def propose_modify_labels(ctx: ServiceContext, params: ModifyLabelsInput) -> Proposal:
    if not params.add_labels and not params.remove_labels and params.mark_read is None:
        raise ValidationFailed("Nothing to change: give add_labels, remove_labels or mark_read.")
    original = _message_header(ctx, params.message_id)
    existing = set(list_labels(ctx, NoInput())["labels"])
    unknown = [name for name in params.add_labels + params.remove_labels if name not in existing]
    if unknown:
        raise ValidationFailed(
            f"Unknown label(s): {', '.join(unknown)}. Only existing user labels are supported."
        )
    changes = []
    if params.add_labels:
        changes.append("add " + ", ".join(params.add_labels))
    if params.remove_labels:
        changes.append("remove " + ", ".join(params.remove_labels))
    if params.mark_read is not None:
        changes.append("mark as read" if params.mark_read else "mark as unread")
    execute_params = params.model_dump()
    return Proposal(
        summary=f"Update \"{original['subject']}\": {'; '.join(changes)}",
        details={"email": original, **execute_params},
        execute_params=execute_params,
    )


# ---------- Write tools: execute (only called after approval) ----------

def execute_send_email(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.send", p)


def execute_create_draft(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.createDraft", p)


def execute_reply_email(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.reply", p)


def execute_create_reply_draft(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.createReplyDraft", p)


def execute_forward_email(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.forward", p)


def execute_modify_labels(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("gmail.modifyLabels", p)
