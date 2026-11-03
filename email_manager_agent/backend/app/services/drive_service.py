"""Google Drive tools. There is no delete or trash operation on purpose."""
from datetime import date
from typing import Any

from pydantic import Field

from app.errors import ValidationFailed
from app.services.common import (
    Proposal,
    ResourceId,
    ServiceContext,
    ToolInput,
    require_dict,
    require_list,
)

# Characters that make a Drive file or folder name awkward or unsafe.
_BAD_NAME_CHARACTERS = set('/\\<>:"|?*\x00')


def _check_name(value: str, what: str) -> str:
    if not value or len(value) > 200 or any(ch in _BAD_NAME_CHARACTERS for ch in value):
        raise ValidationFailed(f"The {what} must be 1-200 characters without / \\ < > : \" | ? *.")
    return value


# ---------- Inputs ----------

class SearchFilesInput(ToolInput):
    name_contains: str = Field("", max_length=200, description="Text the file name contains.")
    full_text: str = Field("", max_length=200, description="Text inside the file content.")
    mime_type: str = Field("", max_length=100, description="Exact MIME type, e.g. application/pdf.")
    modified_after: date | None = Field(None, description="Only files modified on or after this date.")
    max_results: int = Field(10, ge=1, le=100)


class ListFilesInput(ToolInput):
    folder_id: ResourceId | None = Field(None, description="Folder to list; omit for My Drive root.")
    max_results: int = Field(25, ge=1, le=100)


class GetFileInput(ToolInput):
    file_id: ResourceId


class CreateFolderInput(ToolInput):
    name: str = Field(min_length=1, max_length=200)
    parent_folder_id: ResourceId | None = Field(None, description="Parent folder; omit for My Drive root.")


class MoveFileInput(ToolInput):
    file_id: ResourceId
    folder_id: ResourceId = Field(description="Destination folder id.")


class SaveAttachmentsInput(ToolInput):
    message_id: ResourceId = Field(description="Gmail message whose attachments are saved.")
    folder_name: str | None = Field(
        None, max_length=200, description="Drive folder name; omit to use the default attachment folder."
    )


# ---------- Helpers ----------

def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def classify_attachments(
    attachments: list[dict[str, Any]], max_bytes: int, allowed_extensions: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Split attachments into (will_save, skipped_with_reason)."""
    will_save, skipped = [], []
    limit_mb = max_bytes / (1024 * 1024)
    for attachment in attachments:
        name = str(attachment.get("name", ""))
        size = attachment.get("size")
        extension = _extension(name)
        if not isinstance(size, int):
            skipped.append({"name": name, "reason": "Size is unknown."})
        elif size > max_bytes:
            skipped.append(
                {"name": name, "reason": f"Too large ({size / (1024 * 1024):.1f} MB; limit {limit_mb:g} MB)."}
            )
        elif extension not in allowed_extensions:
            skipped.append({"name": name, "reason": f"File type '.{extension or '?'}' is not supported."})
        else:
            will_save.append({"name": name, "size": size})
    return will_save, skipped


# ---------- Read tools ----------

def search_files(ctx: ServiceContext, params: SearchFilesInput) -> dict[str, Any]:
    if not (params.name_contains or params.full_text or params.mime_type or params.modified_after):
        raise ValidationFailed("Give at least one search filter (name, text, type or date).")
    request = params.model_dump(mode="json")
    request["max_results"] = min(params.max_results, ctx.settings.drive_search_max_results)
    data = require_dict(ctx.bridge.call("drive.search", request), "drive.search")
    files = require_list(data.get("files", []), "drive.search")
    return {"count": len(files), "files": files}


def list_files(ctx: ServiceContext, params: ListFilesInput) -> dict[str, Any]:
    request = params.model_dump()
    request["max_results"] = min(params.max_results, ctx.settings.drive_search_max_results)
    return require_dict(ctx.bridge.call("drive.list", request), "drive.list")


def get_file_info(ctx: ServiceContext, params: GetFileInput) -> dict[str, Any]:
    return require_dict(ctx.bridge.call("drive.getFile", {"file_id": params.file_id}), "drive.getFile")


# ---------- Write tools: propose ----------

def propose_create_folder(ctx: ServiceContext, params: CreateFolderInput) -> Proposal:
    _check_name(params.name, "folder name")
    execute_params = params.model_dump()
    return Proposal(
        summary=f"Create Drive folder \"{params.name}\"",
        details=execute_params,
        execute_params=execute_params,
    )


def propose_move_file(ctx: ServiceContext, params: MoveFileInput) -> Proposal:
    file_info = get_file_info(ctx, GetFileInput(file_id=params.file_id))
    folder_info = get_file_info(ctx, GetFileInput(file_id=params.folder_id))
    if not folder_info.get("is_folder"):
        raise ValidationFailed("The destination is not a folder.")
    return Proposal(
        summary=f"Move \"{file_info.get('name', '')}\" to folder \"{folder_info.get('name', '')}\"",
        details={"file": file_info, "destination": folder_info},
        execute_params=params.model_dump(),
    )


def propose_save_attachments(ctx: ServiceContext, params: SaveAttachmentsInput) -> Proposal:
    s = ctx.settings
    folder_name = _check_name(params.folder_name or s.attachment_folder_name, "folder name")
    message = require_dict(
        ctx.bridge.call("gmail.read", {"message_id": params.message_id, "max_body_chars": 0}), "gmail.read"
    )
    attachments = [require_dict(a, "attachment") for a in require_list(message.get("attachments", []), "attachments")]
    if not attachments:
        raise ValidationFailed("That email has no attachments.")
    will_save, skipped = classify_attachments(attachments, s.attachment_max_bytes, s.supported_attachment_extensions)
    if not will_save:
        reasons = "; ".join(f"{a['name']}: {a['reason']}" for a in skipped)
        raise ValidationFailed(f"None of the attachments can be saved. {reasons}")
    warnings = [f"Will skip {a['name']}: {a['reason']}" for a in skipped]
    return Proposal(
        summary=f"Save {len(will_save)} attachment(s) from \"{message.get('subject', '')}\" to Drive folder \"{folder_name}\"",
        details={"email_subject": message.get("subject", ""), "folder_name": folder_name,
                 "will_save": will_save, "will_skip": skipped},
        execute_params={
            "message_id": params.message_id,
            "folder_name": folder_name,
            "max_bytes": s.attachment_max_bytes,
            "allowed_extensions": s.supported_attachment_extensions,
        },
        warnings=warnings,
    )


# ---------- Write tools: execute ----------

def execute_create_folder(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("drive.createFolder", p)


def execute_move_file(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("drive.moveFile", p)


def execute_save_attachments(ctx: ServiceContext, p: dict[str, Any]) -> Any:
    return ctx.bridge.call("drive.saveAttachments", p)
