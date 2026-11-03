"""End-to-end workflows over real HTTP: a real uvicorn server, with Claude and Google faked.

Covers the four workflows from the project brief: find emails, send an email with
approval, schedule a meeting after an availability check, and find a Drive file.
"""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.dependencies import Container
from app.main import create_app
from app.sessions import SessionStore

from tests.conftest import FakeBridge, FakeClaude, response, text_block, tool_block


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(settings, now_fn):
    bridge, claude = FakeBridge(), FakeClaude()
    container = Container(settings=settings, bridge=bridge, model=claude,
                          sessions=SessionStore(3600, now_fn), now=now_fn)
    port = _free_port()
    config = uvicorn.Config(create_app(container), host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not uv.started and time.time() < deadline:
        time.sleep(0.05)
    assert uv.started, "server did not start"
    with httpx.Client(base_url=f"http://localhost:{port}", timeout=10) as http:
        yield http, bridge, claude
    uv.should_exit = True
    thread.join(timeout=5)


def test_gmail_search_workflow(server):
    http, bridge, claude = server
    bridge.on("gmail.search", {"messages": [{"id": "m1", "from": "alice@example.com", "subject": "Report", "unread": True}]})
    claude.script += [
        response(tool_block("t1", "search_emails", {"query": "is:unread newer_than:1d"})),
        response(text_block("1 unread email today: Report from alice@example.com.")),
    ]
    data = http.post("/api/prompt", json={"prompt": "Find unread emails from today"}).json()
    assert "Report" in data["reply"] and data["pending_operations"] == []
    assert bridge.ops() == ["gmail.search"]


def test_send_email_with_approval_workflow(server):
    http, bridge, claude = server
    bridge.on("gmail.send", {"sent": True})
    claude.script += [
        response(tool_block("t1", "send_email", {"to": ["bob@example.com"], "subject": "Lunch", "body": "Noon?"})),
        response(text_block("I've prepared it. Please approve.")),
    ]
    data = http.post("/api/prompt", json={"prompt": "Email Bob about lunch"}).json()
    op = data["pending_operations"][0]
    assert bridge.ops() == []
    confirm = http.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op["id"], "decision": "approve"})
    assert confirm.json()["status"] == "executed"
    assert bridge.ops() == ["gmail.send"]


def test_schedule_meeting_workflow(server):
    http, bridge, claude = server
    bridge.on("calendar.listEvents", {"events": [
        {"id": "e1", "title": "Standup", "start": "2026-11-10T09:00:00Z", "end": "2026-11-10T09:30:00Z", "all_day": False, "my_status": "YES"},
    ]})
    bridge.on("calendar.createEvent", {"id": "new1", "title": "Planning"})
    claude.script += [
        response(tool_block("t1", "find_available_slots", {"duration_minutes": 30, "from_date": "2026-11-10", "to_date": "2026-11-10"})),
        response(tool_block("t2", "create_event", {"title": "Planning", "start": "2026-11-10T09:30:00Z", "end": "2026-11-10T10:00:00Z"})),
        response(text_block("Proposed Planning at 09:30. Please approve.")),
    ]
    data = http.post("/api/prompt", json={"prompt": "Schedule 30 min planning tomorrow"}).json()
    # The first free slot after the 09:00 standup is 09:30.
    tool_result = claude.requests[1][-1]["content"][0]["content"]
    assert '"start": "2026-11-10T09:30:00+00:00"' in tool_result
    op = data["pending_operations"][0]
    assert op["tool_name"] == "create_event" and op["warnings"] == []
    confirm = http.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op["id"], "decision": "approve"}).json()
    assert confirm["status"] == "executed"
    assert bridge.ops()[-1] == "calendar.createEvent"


def test_drive_search_workflow(server):
    http, bridge, claude = server
    bridge.on("drive.search", {"files": [{"id": "f1", "name": "Invoice-Oct.pdf", "url": "https://drive.google.com/f1"}]})
    claude.script += [
        response(tool_block("t1", "search_files", {"name_contains": "Invoice", "mime_type": "application/pdf"})),
        response(text_block("Found Invoice-Oct.pdf.")),
    ]
    data = http.post("/api/prompt", json={"prompt": "Find invoice PDFs"}).json()
    assert data["reply"] == "Found Invoice-Oct.pdf."
    assert bridge.calls[0][1]["name_contains"] == "Invoice"
