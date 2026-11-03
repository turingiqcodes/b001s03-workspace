"""The tools Claude can call, and whether each one reads or writes.

Read tools run immediately. Write tools never run during the agent loop: they are
validated and turned into a pending operation that only POST /api/confirm can execute.
Claude cannot change a tool's kind.
"""
from dataclasses import dataclass
from typing import Any, Callable, Literal

from app.services import calendar_service as cal
from app.services import drive_service as drive
from app.services import gmail_service as gmail
from app.services.common import Proposal, ServiceContext, ToolInput

ToolKind = Literal["read", "write"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    kind: ToolKind
    run: Callable[[ServiceContext, Any], dict[str, Any]] | None = None        # read tools
    propose: Callable[[ServiceContext, Any], Proposal] | None = None          # write tools
    execute: Callable[[ServiceContext, dict[str, Any], list[str]], Any] | None = None  # write tools

    def to_claude_tool(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": _clean_schema(self.input_model)}


def _clean_schema(model: type[ToolInput]) -> dict[str, Any]:
    """Pydantic JSON schema without its generated 'title' labels.

    Only string-valued 'title' keys are labels; a property *named* title has a dict value and is kept.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(model.model_json_schema())


def _no_ack(execute: Callable[[ServiceContext, dict[str, Any]], Any]):
    """Adapt an execute function that does not care about acknowledged conflicts."""
    return lambda ctx, params, _acknowledged: execute(ctx, params)


WRITE_NOTE = " This is a write: it is NOT done when you call it. The user must approve it first."

TOOL_SPECS: list[ToolSpec] = [
    # ----- Gmail -----
    ToolSpec("search_emails", "Search Gmail with Gmail search syntax. Returns id, from, subject, date, snippet, unread.",
             gmail.SearchEmailsInput, "read", run=gmail.search_emails),
    ToolSpec("read_email", "Read one email: headers, plain-text body (may be truncated) and attachment list.",
             gmail.ReadEmailInput, "read", run=gmail.read_email),
    ToolSpec("list_labels", "List the user's Gmail labels.", gmail.NoInput, "read", run=gmail.list_labels),
    ToolSpec("send_email", "Propose sending a new email." + WRITE_NOTE, gmail.ComposeEmailInput, "write",
             propose=gmail.propose_send_email, execute=_no_ack(gmail.execute_send_email)),
    ToolSpec("create_draft", "Propose saving a new email as a Gmail draft." + WRITE_NOTE, gmail.ComposeEmailInput,
             "write", propose=gmail.propose_create_draft, execute=_no_ack(gmail.execute_create_draft)),
    ToolSpec("reply_email", "Propose replying to an email (sends the reply)." + WRITE_NOTE, gmail.ReplyEmailInput,
             "write", propose=gmail.propose_reply_email, execute=_no_ack(gmail.execute_reply_email)),
    ToolSpec("create_reply_draft", "Propose saving a reply to an email as a draft, without sending." + WRITE_NOTE,
             gmail.ReplyEmailInput, "write", propose=gmail.propose_create_reply_draft,
             execute=_no_ack(gmail.execute_create_reply_draft)),
    ToolSpec("forward_email", "Propose forwarding an email." + WRITE_NOTE, gmail.ForwardEmailInput, "write",
             propose=gmail.propose_forward_email, execute=_no_ack(gmail.execute_forward_email)),
    ToolSpec("modify_email_labels", "Propose adding/removing existing user labels or marking read/unread." + WRITE_NOTE,
             gmail.ModifyLabelsInput, "write", propose=gmail.propose_modify_labels,
             execute=_no_ack(gmail.execute_modify_labels)),
    # ----- Calendar -----
    ToolSpec("list_events", "List calendar events between two times, optionally matching text.",
             cal.ListEventsInput, "read", run=cal.list_events),
    ToolSpec("get_event", "Get one calendar event's details.", cal.GetEventInput, "read", run=cal.get_event),
    ToolSpec("find_available_slots",
             "Find free slots of a given length inside working hours, from the real calendar.",
             cal.FindSlotsInput, "read", run=cal.find_available_slots),
    ToolSpec("create_event", "Propose creating a calendar event. Conflicts are checked automatically." + WRITE_NOTE,
             cal.CreateEventInput, "write", propose=cal.propose_create_event, execute=cal.execute_create_event),
    ToolSpec("update_event",
             "Propose changing or moving an event. If only new_start is given the duration is kept." + WRITE_NOTE,
             cal.UpdateEventInput, "write", propose=cal.propose_update_event, execute=cal.execute_update_event),
    ToolSpec("cancel_event", "Propose cancelling an event." + WRITE_NOTE, cal.CancelEventInput, "write",
             propose=cal.propose_cancel_event, execute=cal.execute_cancel_event),
    # ----- Drive -----
    ToolSpec("search_files", "Search Google Drive by name, content text, MIME type or modified date.",
             drive.SearchFilesInput, "read", run=drive.search_files),
    ToolSpec("list_files", "List files and folders in a Drive folder (default: My Drive root).",
             drive.ListFilesInput, "read", run=drive.list_files),
    ToolSpec("get_file_info", "Get details of one Drive file or folder.", drive.GetFileInput, "read",
             run=drive.get_file_info),
    ToolSpec("create_folder", "Propose creating a Drive folder." + WRITE_NOTE, drive.CreateFolderInput, "write",
             propose=drive.propose_create_folder, execute=_no_ack(drive.execute_create_folder)),
    ToolSpec("move_file", "Propose moving a Drive file into a folder." + WRITE_NOTE, drive.MoveFileInput, "write",
             propose=drive.propose_move_file, execute=_no_ack(drive.execute_move_file)),
    ToolSpec("save_email_attachments",
             "Propose saving an email's attachments into a Drive folder. Oversized or unsupported files are skipped."
             + WRITE_NOTE,
             drive.SaveAttachmentsInput, "write", propose=drive.propose_save_attachments,
             execute=_no_ack(drive.execute_save_attachments)),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def claude_tool_definitions() -> list[dict[str, Any]]:
    return [spec.to_claude_tool() for spec in TOOL_SPECS]
