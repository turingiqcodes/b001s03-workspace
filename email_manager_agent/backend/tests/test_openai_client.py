"""OpenAI-compatible (Groq) client: message conversion, response parsing and error mapping (no real server)."""
import json

import httpx
import pytest

from app.agent.agent_loop import Agent
from app.agent.openai_client import OpenAICompatibleClient, from_openai_response, to_openai_messages
from app.agent.tools import claude_tool_definitions
from app.dependencies import build_model
from app.errors import ConfigurationError, ModelError
from app.services.common import ServiceContext
from app.sessions import SessionStore

from tests.conftest import make_settings

OPENAI_SETTINGS = {"llm_provider": "openai", "openai_api_key": "test-groq-key", "openai_model": "test-groq-model"}


def openai_settings(**overrides):
    return make_settings(**{**OPENAI_SETTINGS, **overrides})


def client_with(handler, **overrides):
    return OpenAICompatibleClient(openai_settings(**overrides), transport=httpx.MockTransport(handler))


def reply(content=None, tool_calls=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"id": "x", "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}


def call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_provider_selects_this_client_and_reports_issues():
    assert isinstance(build_model(openai_settings()), OpenAICompatibleClient)
    settings = openai_settings(openai_api_key=None, openai_model=None)
    issues = " ".join(settings.llm_config_issues())
    assert "OPENAI_API_KEY" in issues and "OPENAI_MODEL" in issues
    assert openai_settings().active_model == "test-groq-model"
    assert openai_settings().openai_base_url == "https://api.groq.com/openai/v1"


def test_history_conversion_uses_tool_call_ids():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "find mail"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "search_emails", "input": {"query": "is:unread"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"count": 0}'},
            {"type": "tool_result", "tool_use_id": "call_2", "content": "bad", "is_error": True},
            {"type": "text", "text": "thanks"},
        ]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "None found."}]},
    ]
    out = to_openai_messages("SYSTEM", history)
    assert out[0] == {"role": "system", "content": "SYSTEM"}
    assert out[1] == {"role": "user", "content": "find mail"}
    assert out[2] == {"role": "assistant", "content": None, "tool_calls": [
        call("call_1", "search_emails", '{"query": "is:unread"}'),
    ]}
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"count": 0}'}
    assert out[4] == {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: bad"}
    assert out[5] == {"role": "user", "content": "thanks"}
    assert out[6] == {"role": "assistant", "content": "None found."}


def test_response_with_tool_calls():
    result = from_openai_response(reply(tool_calls=[
        call("c1", "search_emails", '{"query": "x"}'),
        call("c2", "read_email", "not json"),
    ], finish_reason="tool_calls"))
    assert result.stop_reason == "tool_use"
    uses = [b for b in result.content if b["type"] == "tool_use"]
    assert [(u["id"], u["input"]) for u in uses] == [("c1", {"query": "x"}), ("c2", "not json")]


def test_response_text_and_length():
    assert from_openai_response(reply("Hello")).stop_reason == "end_turn"
    assert from_openai_response(reply("cut", finish_reason="length")).stop_reason == "max_tokens"
    with pytest.raises(ModelError):
        from_openai_response({"oops": 1})


def test_request_sent_with_key_and_tools():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=reply("hi"))

    result = client_with(handler).create(
        system="S", tools=claude_tool_definitions()[:1], messages=[{"role": "user", "content": "hello"}]
    )
    assert result.content == [{"type": "text", "text": "hi"}]
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer test-groq-key"
    body = seen["body"]
    assert body["model"] == "test-groq-model" and body["max_completion_tokens"] == 4096
    assert body["tools"][0]["function"]["name"] == "search_emails" and body["tool_choice"] == "auto"
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.parametrize("status,error,expected_type,text", [
    (401, {"message": "Invalid API Key", "code": "invalid_api_key"}, ConfigurationError, "OPENAI_API_KEY"),
    (404, {"message": "model does not exist", "code": "model_not_found"}, ConfigurationError, "OPENAI_MODEL"),
    (429, {"message": "Rate limit reached", "code": "rate_limit_exceeded"}, ModelError, "rate limiting"),
    (413, {"message": "Request too large", "code": "request_too_large"}, ModelError, "too large"),
    (400, {"message": "Failed to call a function", "code": "tool_use_failed"}, ModelError, "invalid tool call"),
    (500, {"message": "boom"}, ModelError, "HTTP 500: boom"),
])
def test_error_mapping(status, error, expected_type, text):
    client = client_with(lambda r: httpx.Response(status, json={"error": error}))
    with pytest.raises(expected_type, match=text):
        client.create(system="S", tools=[], messages=[{"role": "user", "content": "x"}])


def test_rate_limit_is_http_429():
    client = client_with(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(ModelError) as info:
        client.create(system="S", tools=[], messages=[])
    assert info.value.http_status == 429


def test_network_errors():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelError, match="internet connection"):
        client_with(refuse).create(system="S", tools=[], messages=[])
    with pytest.raises(ModelError, match="did not answer"):
        client_with(slow).create(system="S", tools=[], messages=[])


def test_missing_key_does_not_call_server():
    calls = []
    client = client_with(lambda r: calls.append(r) or httpx.Response(200), openai_api_key=None)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        client.create(system="S", tools=[], messages=[])
    assert calls == []


def test_check_finds_model():
    models = {"data": [{"id": "test-groq-model"}, {"id": "other"}]}
    client_with(lambda r: httpx.Response(200, json=models)).check()  # no error
    with pytest.raises(ConfigurationError, match="not available"):
        client_with(lambda r: httpx.Response(200, json=models), openai_model="missing").check()


def test_full_agent_turn_through_openai_client(bridge, now_fn):
    """A search -> answer turn with the real agent loop and the OpenAI wire format."""
    settings = openai_settings()
    bridge.on("gmail.search", {"messages": [{"id": "m1", "subject": "Report"}]})
    replies = [
        reply(tool_calls=[call("c1", "search_emails", '{"query": "is:unread"}')], finish_reason="tool_calls"),
        reply("You have one unread email: Report."),
    ]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    agent = Agent(OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler)),
                  ServiceContext(settings=settings, bridge=bridge, now=now_fn))
    session = SessionStore(3600, now_fn).get_or_create(None)
    result = agent.run(session, "Any unread email?")
    assert result.reply == "You have one unread email: Report."
    tool_message = requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert json.loads(tool_message["content"])["messages"][0]["subject"] == "Report"


def test_short_rate_limit_waits_and_retries():
    responses = [
        httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "TPM"}}),
        httpx.Response(200, json=reply("ok")),
    ]
    waits = []
    client = OpenAICompatibleClient(
        openai_settings(), transport=httpx.MockTransport(lambda r: responses.pop(0)), sleep=waits.append
    )
    assert client.create(system="S", tools=[], messages=[]).content == [{"type": "text", "text": "ok"}]
    assert waits == [3.5]


def test_long_or_repeated_rate_limit_is_reported():
    waits = []
    long_wait = httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"message": "TPD"}})
    client = OpenAICompatibleClient(openai_settings(), transport=httpx.MockTransport(lambda r: long_wait), sleep=waits.append)
    with pytest.raises(ModelError, match="rate limiting"):
        client.create(system="S", tools=[], messages=[])
    assert waits == []  # too long to wait inside a request

    calls = []

    def always_limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "TPM"}})

    client = OpenAICompatibleClient(openai_settings(), transport=httpx.MockTransport(always_limited), sleep=waits.append)
    with pytest.raises(ModelError, match="rate limiting"):
        client.create(system="S", tools=[], messages=[])
    assert len(calls) == 3 and waits == [1.5, 1.5]
