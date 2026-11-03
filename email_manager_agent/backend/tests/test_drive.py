"""Drive service: search, listing, folders, moves and attachment handling."""
import pytest

from app.errors import BridgeError, ValidationFailed
from app.services import drive_service as drive

MB = 1024 * 1024


def message_with(*attachments):
    return {"id": "m1", "subject": "Invoices", "attachments": list(attachments)}


def test_search_needs_a_filter(ctx):
    with pytest.raises(ValidationFailed):
        drive.search_files(ctx, drive.SearchFilesInput())


def test_search_passes_structured_filters(ctx, bridge, settings):
    settings.drive_search_max_results = 5
    bridge.on("drive.search", {"files": [{"id": "f1", "name": "Budget.xlsx"}]})
    out = drive.search_files(ctx, drive.SearchFilesInput(name_contains="Budget", modified_after="2026-01-01", max_results=50))
    assert out["count"] == 1
    sent = bridge.calls[0][1]
    assert sent["name_contains"] == "Budget" and sent["modified_after"] == "2026-01-01" and sent["max_results"] == 5


def test_list_and_info(ctx, bridge):
    bridge.on("drive.list", {"folder": {"id": "root", "name": "My Drive"}, "files": [], "folders": []})
    bridge.on("drive.getFile", {"id": "f1", "name": "a.pdf", "is_folder": False})
    assert drive.list_files(ctx, drive.ListFilesInput())["folder"]["name"] == "My Drive"
    assert drive.get_file_info(ctx, drive.GetFileInput(file_id="f1"))["name"] == "a.pdf"


def test_bridge_failure(ctx, bridge):
    bridge.on("drive.list", BridgeError("Drive unavailable"))
    with pytest.raises(BridgeError):
        drive.list_files(ctx, drive.ListFilesInput())


def test_classify_attachments():
    save, skip = drive.classify_attachments(
        [{"name": "a.pdf", "size": 1 * MB}, {"name": "big.pdf", "size": 30 * MB},
         {"name": "run.exe", "size": 10}, {"name": "noext", "size": 10}, {"name": "x.pdf", "size": None}],
        max_bytes=10 * MB, allowed_extensions=["pdf"],
    )
    assert save == [{"name": "a.pdf", "size": MB}]
    reasons = {s["name"]: s["reason"] for s in skip}
    assert "Too large" in reasons["big.pdf"] and "not supported" in reasons["run.exe"]
    assert "not supported" in reasons["noext"] and "unknown" in reasons["x.pdf"]


def test_save_attachments_proposal_uses_default_folder(ctx, bridge, settings):
    bridge.on("gmail.read", message_with({"name": "inv.pdf", "size": 2 * MB}, {"name": "huge.zip", "size": 50 * MB}))
    proposal = drive.propose_save_attachments(ctx, drive.SaveAttachmentsInput(message_id="m1"))
    assert proposal.details["folder_name"] == settings.attachment_folder_name == "Email Attachments"
    assert proposal.details["will_save"] == [{"name": "inv.pdf", "size": 2 * MB}]
    assert "Will skip huge.zip" in proposal.warnings[0]
    assert proposal.execute_params["max_bytes"] == settings.attachment_max_bytes
    assert proposal.execute_params["allowed_extensions"] == settings.supported_attachment_extensions


def test_save_attachments_nothing_savable(ctx, bridge):
    bridge.on("gmail.read", message_with({"name": "a.exe", "size": 10}))
    with pytest.raises(ValidationFailed, match="None of the attachments"):
        drive.propose_save_attachments(ctx, drive.SaveAttachmentsInput(message_id="m1"))
    bridge.on("gmail.read", message_with())
    with pytest.raises(ValidationFailed, match="no attachments"):
        drive.propose_save_attachments(ctx, drive.SaveAttachmentsInput(message_id="m1"))


def test_folder_name_validation(ctx):
    with pytest.raises(ValidationFailed):
        drive.propose_create_folder(ctx, drive.CreateFolderInput(name="a/b"))
    assert drive.propose_create_folder(ctx, drive.CreateFolderInput(name="Receipts")).summary == 'Create Drive folder "Receipts"'


def test_move_requires_folder_destination(ctx, bridge):
    bridge.on("drive.getFile", lambda p: {"id": p["file_id"], "name": p["file_id"], "is_folder": p["file_id"] == "folder1"})
    with pytest.raises(ValidationFailed, match="not a folder"):
        drive.propose_move_file(ctx, drive.MoveFileInput(file_id="f1", folder_id="f2"))
    proposal = drive.propose_move_file(ctx, drive.MoveFileInput(file_id="f1", folder_id="folder1"))
    assert proposal.execute_params == {"file_id": "f1", "folder_id": "folder1"}


def test_execute_functions(ctx, bridge):
    for op in ("drive.createFolder", "drive.moveFile", "drive.saveAttachments"):
        bridge.on(op, {"done": op})
    drive.execute_create_folder(ctx, {})
    drive.execute_move_file(ctx, {})
    assert drive.execute_save_attachments(ctx, {}) == {"done": "drive.saveAttachments"}
