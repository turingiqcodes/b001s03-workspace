"""Ollama client: message/tool conversion, response parsing and error mapping (no real server)."""
import json

import httpx
import pytest

from app.agent.agent_loop import Agent
from app.agent.ollama_client import (
    OllamaClient, from_ollama_response, to_ollama_messages, to_ollama_tools,
)
from app.agent.tools import claude_tool_definitions
from app.errors import ConfigurationError, ModelError
from app.services.common import ServiceContext
from app.sessions import SessionStore

from tests.conftest import make_settings


def client_with(handler, **settings):
    return OllamaClient(make_settings(**settings), transport=httpx.MockTransport(handler))


def ollama_reply(content="", tool_calls=None, done_reason="stop"):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"model": "test-model", "message": message, "done": True, "done_reason": done_reason}


def test_tool_definitions_convert_to_functions():
    tools = to_ollama_tools(claude_tool_definitions())
    assert len(tools) == 21
    first = tools[0]
    assert first["type"] == "function"
    assert first["function"]["name"] == "search_emails"
    assert first["function"]["parameters"]["type"] == "object"


def test_history_conversion_matches_tool_results_to_names():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "find mail"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Searching."},
            {"type": "tool_use", "id": "call_1", "name": "search_emails", "input": {"query": "is:unread"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"count": 0}'},
            {"type": "tool_result", "tool_use_id": "call_x", "content": "bad", "is_error": True},
            {"type": "text", "text": "thanks"},
        ]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "None found."}]},
    ]
    out = to_ollama_messages("SYSTEM", history)
    assert out[0] == {"role": "system", "content": "SYSTEM"}
    assert out[1] == {"role": "user", "content": "find mail"}
    assert out[2] == {"role": "assistant", "content": "Searching.",
                      "tool_calls": [{"function": {"name": "search_emails", "arguments": {"query": "is:unread"}}}]}
    assert out[3] == {"role": "tool", "tool_name": "search_emails", "content": '{"count": 0}'}
    assert out[4] == {"role": "tool", "tool_name": "", "content": "ERROR: bad"}
    assert out[5] == {"role": "user", "content": "thanks"}
    assert out[6] == {"role": "assistant", "content": "None found."}  # thinking blocks dropped


def test_response_with_tool_calls():
    result = from_ollama_response(ollama_reply(tool_calls=[
        {"function": {"name": "search_emails", "arguments": {"query": "x"}}},
        {"function": {"name": "read_email", "arguments": '{"message_id": "m1"}'}},  # string form
        {"function": {"name": "read_email", "arguments": "not json"}},
    ]))
    assert result.stop_reason == "tool_use"
    uses = [b for b in result.content if b["type"] == "tool_use"]
    assert [u["input"] for u in uses] == [{"query": "x"}, {"message_id": "m1"}, "not json"]
    assert len({u["id"] for u in uses}) == 3  # generated ids are unique


def test_response_text_and_length():
    assert from_ollama_response(ollama_reply("Hello")).stop_reason == "end_turn"
    assert from_ollama_response(ollama_reply("cut", done_reason="length")).stop_reason == "max_tokens"
    with pytest.raises(ModelError):
        from_ollama_response({"oops": 1})


def test_request_body_sent_to_ollama():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ollama_reply("hi"))

    client = client_with(handler, ollama_num_ctx=8192)
    result = client.create(system="S", tools=claude_tool_definitions()[:1], messages=[{"role": "user", "content": "hello"}])
    assert result.content == [{"type": "text", "text": "hi"}]
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "test-model" and body["stream"] is False
    assert body["options"] == {"num_ctx": 8192}
    assert body["keep_alive"] == "30m"
    assert "think" not in body  # only sent when OLLAMA_THINK is set
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


def test_think_option_is_sent_when_configured():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ollama_reply("ok"))

    client_with(handler, ollama_think=False).create(system="S", tools=[], messages=[{"role": "user", "content": "x"}])
    assert seen["body"]["think"] is False


@pytest.mark.parametrize("status,error,expected_type,text", [
    (404, "model 'test-model' not found", ConfigurationError, "ollama pull test-model"),
    (400, "registry.ollama.ai/library/test-model does not support tools", ConfigurationError, "tool calling"),
    (400, "test-model does not support thinking", ConfigurationError, "OLLAMA_THINK"),
    (500, "out of memory", ModelError, "out of memory"),
])
def test_error_mapping(status, error, expected_type, text):
    client = client_with(lambda r: httpx.Response(status, json={"error": error}))
    with pytest.raises(expected_type, match=text):
        client.create(system="S", tools=[], messages=[{"role": "user", "content": "x"}])


def test_server_not_running():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConfigurationError, match="ollama serve"):
        client_with(handler).create(system="S", tools=[], messages=[])


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelError, match="OLLAMA_TIMEOUT_SECONDS"):
        client_with(handler).create(system="S", tools=[], messages=[])


def test_missing_model_setting_does_not_call_server():
    calls = []
    client = client_with(lambda r: calls.append(r) or httpx.Response(200), ollama_model=None)
    with pytest.raises(ConfigurationError, match="OLLAMA_MODEL"):
        client.create(system="S", tools=[], messages=[])
    assert calls == []


def test_check_finds_pulled_model():
    tags = {"models": [{"name": "test-model:latest"}, {"name": "llama3.1:8b"}]}
    client_with(lambda r: httpx.Response(200, json=tags)).check()  # no error
    with pytest.raises(ConfigurationError, match="ollama pull qwen3:8b"):
        client_with(lambda r: httpx.Response(200, json=tags), ollama_model="qwen3:8b").check()


def test_full_agent_turn_through_ollama_client(bridge, now_fn):
    """A search -> answer turn with the real agent loop and the Ollama wire format."""
    settings = make_settings()
    bridge.on("gmail.search", {"messages": [{"id": "m1", "subject": "Report"}]})
    replies = [
        ollama_reply(tool_calls=[{"function": {"name": "search_emails", "arguments": {"query": "is:unread"}}}]),
        ollama_reply("You have one unread email: Report."),
    ]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    agent = Agent(OllamaClient(settings, transport=httpx.MockTransport(handler)),
                  ServiceContext(settings=settings, bridge=bridge, now=now_fn))
    session = SessionStore(3600, now_fn).get_or_create(None)
    result = agent.run(session, "Any unread email?")
    assert result.reply == "You have one unread email: Report."
    tool_message = requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_name"] == "search_emails"
    assert json.loads(tool_message["content"])["messages"][0]["subject"] == "Report"
