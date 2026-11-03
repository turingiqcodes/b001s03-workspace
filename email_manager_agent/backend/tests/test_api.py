"""Endpoint tests: status, prompt, confirm, reset, validation and error format."""
from app.errors import BridgeTimeout
from app.main import create_app

from tests.conftest import FakeClaude, make_client, response, text_block, tool_block


def _prompt(client, prompt="hello", session_id=None):
    body = {"prompt": prompt}
    if session_id:
        body["session_id"] = session_id
    return client.post("/api/prompt", json=body)


def _propose_send(claude: FakeClaude):
    claude.script += [
        response(tool_block("t1", "send_email", {"to": ["bob@example.com"], "subject": "Hi", "body": "Hello Bob"})),
        response(text_block("I've prepared the email. Please approve it.")),
    ]


# ----- status -----

def test_status_reports_configuration(client):
    data = client.get("/api/status").json()
    assert data["status"] == "ok"
    assert data["llm"] == {"configured": True, "issues": [], "provider": "ollama", "model": "test-model", "reachable": None}
    assert data["bridge"]["configured"] is True and data["bridge"]["reachable"] is None
    assert data["limits"]["max_agent_steps"] == 10


def test_status_checks_bridge_on_request(client, bridge):
    data = client.get("/api/status", params={"check_bridge": "true"}).json()
    assert data["bridge"]["reachable"] is True
    assert data["bridge"]["account"] == "me@example.com"

    bridge.on("ping", BridgeTimeout("Google did not answer"))
    data = client.get("/api/status", params={"check_bridge": "true"}).json()
    assert data["bridge"]["reachable"] is False
    assert "did not answer" in data["bridge"]["issues"][0]


def test_status_lists_missing_configuration(container, client):
    container.settings.ollama_model = None
    container.settings.apps_script_url = None
    data = client.get("/api/status").json()
    assert data["llm"]["configured"] is False
    assert any("OLLAMA_MODEL" in issue for issue in data["llm"]["issues"])
    assert any("APPS_SCRIPT_URL" in issue for issue in data["bridge"]["issues"])


# ----- prompt -----

def test_prompt_returns_reply_and_session(client, claude):
    claude.script.append(response(text_block("Hi there")))
    res = _prompt(client)
    assert res.status_code == 200
    data = res.json()
    assert data["reply"] == "Hi there"
    assert data["steps"] == 1 and data["stop_reason"] == "end_turn"
    assert len(data["session_id"]) >= 16
    assert data["pending_operations"] == []


def test_session_keeps_context_between_prompts(client, claude):
    claude.script += [response(text_block("first")), response(text_block("second"))]
    sid = _prompt(client, "one").json()["session_id"]
    assert _prompt(client, "two", sid).json()["session_id"] == sid
    second_request = claude.requests[1]
    assert [m["role"] for m in second_request] == ["user", "assistant", "user"]


def test_prompt_validation(client, container):
    assert client.post("/api/prompt", json={}).status_code == 422
    assert client.post("/api/prompt", json={"prompt": ""}).status_code == 422
    assert client.post("/api/prompt", json={"prompt": "hi", "extra": 1}).status_code == 422
    res = _prompt(client, "x" * (container.settings.prompt_max_chars + 1))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"
    assert _prompt(client, "hi", session_id="bad id!").status_code == 422


def test_prompt_reports_missing_ollama_config(container, client):
    container.settings.ollama_model = None
    from app.agent.ollama_client import OllamaClient

    container.model = OllamaClient(container.settings)
    res = _prompt(client)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "configuration_error"
    assert "OLLAMA_MODEL" in res.json()["error"]["message"]


def test_prompt_reports_missing_claude_config(container, client):
    container.settings.llm_provider = "claude"
    container.settings.anthropic_api_key = None
    from app.agent.claude_client import ClaudeClient

    container.model = ClaudeClient(container.settings)
    res = _prompt(client)
    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.json()["error"]["message"]
    status = client.get("/api/status").json()["llm"]
    assert status["provider"] == "claude" and status["model"] == "test-claude-model"


def test_unknown_session_id_starts_new_session(client, claude):
    claude.script.append(response(text_block("ok")))
    data = _prompt(client, "hi", session_id="A" * 32).json()
    assert data["session_id"] != "A" * 32


# ----- confirm -----

def test_confirm_approve_executes_stored_operation(client, claude, bridge):
    bridge.on("gmail.send", {"sent": True})
    _propose_send(claude)
    data = _prompt(client, "email bob").json()
    assert "gmail.send" not in bridge.ops()  # nothing sent before approval
    op = data["pending_operations"][0]
    assert op["tool_name"] == "send_email" and "execute_params" not in op

    res = client.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op["id"], "decision": "approve"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "executed" and body["result"] == {"sent": True}
    assert body["pending_operations"] == []
    assert bridge.calls[-1] == ("gmail.send", {"to": ["bob@example.com"], "cc": [], "bcc": [], "subject": "Hi", "body": "Hello Bob"})


def test_confirm_requires_existing_session(client):
    res = client.post("/api/confirm", json={"session_id": "B" * 32, "operation_id": "op_abcdefghijkl", "decision": "approve"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_confirm_validates_body(client):
    assert client.post("/api/confirm", json={"session_id": "B" * 32, "operation_id": "op_abcdefghijkl", "decision": "maybe"}).status_code == 422
    assert client.post("/api/confirm", json={"session_id": "B" * 32, "operation_id": "drop table", "decision": "approve"}).status_code == 422


def test_approval_note_reaches_claude_on_next_prompt(client, claude, bridge):
    bridge.on("gmail.send", {"sent": True})
    _propose_send(claude)
    data = _prompt(client, "email bob").json()
    op_id = data["pending_operations"][0]["id"]
    client.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op_id, "decision": "reject"})

    claude.script.append(response(text_block("Understood")))
    _prompt(client, "ok", data["session_id"])
    last_user_text = claude.requests[-1][-1]["content"][-1]["text"]
    assert "[Approval update]" in last_user_text and "REJECTED" in last_user_text and op_id in last_user_text


# ----- reset -----

def test_reset_clears_history_and_pending_operations(client, claude):
    _propose_send(claude)
    data = _prompt(client, "email bob").json()
    res = client.post("/api/reset", json={"session_id": data["session_id"]})
    assert res.status_code == 200
    new_id = res.json()["session_id"]
    assert new_id != data["session_id"]

    # The old session and its pending operation are gone.
    op_id = data["pending_operations"][0]["id"]
    res = client.post("/api/confirm", json={"session_id": data["session_id"], "operation_id": op_id, "decision": "approve"})
    assert res.status_code == 404
    assert client.get("/api/status", params={"session_id": new_id}).json()["pending_operations"] == []


def test_reset_without_session(client):
    res = client.post("/api/reset", json={})
    assert res.status_code == 200 and res.json()["status"] == "reset"


# ----- localhost restrictions and error format -----

def test_rejects_non_local_host_header(container):
    remote = make_client(create_app(container), host="evil.example.com")
    assert remote.get("/api/status").status_code == 400


def test_rejects_non_loopback_client(container):
    remote = make_client(create_app(container), client_ip="192.168.1.20")
    res = remote.get("/api/status")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


def test_unexpected_errors_are_hidden(container, claude):
    claude.script.append(RuntimeError("secret internal detail"))
    safe_client = make_client(create_app(container), raise_server_exceptions=False)
    res = safe_client.post("/api/prompt", json={"prompt": "hi"})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in res.text


def test_status_checks_model_server(container, client):
    from app.errors import ConfigurationError

    class CheckableModel:
        def __init__(self, error=None):
            self.error = error

        def check(self):
            if self.error:
                raise self.error

    container.model = CheckableModel()
    assert client.get("/api/status", params={"check_bridge": "true"}).json()["llm"]["reachable"] is True
    container.model = CheckableModel(ConfigurationError("Cannot connect to Ollama at http://127.0.0.1:11434."))
    llm = client.get("/api/status", params={"check_bridge": "true"}).json()["llm"]
    assert llm["reachable"] is False and "Cannot connect to Ollama" in llm["issues"][0]
