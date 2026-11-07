"""Groq provider against recorded OpenAI-compatible responses (FR-030 as amended by
D-08, FR-033/034; scenario "AI provider timeout")."""
from __future__ import annotations

import httpx
import pytest

from oncall_agent.config import AISettings
from oncall_agent.exceptions import AIProviderError
from oncall_agent.providers.base import AIProvider, AnalysisPrompt
from oncall_agent.providers.groq import GroqProvider

from .fakes import FakeServer, raise_timeout

pytestmark = pytest.mark.integration

PATH = "/openai/v1/chat/completions"
PROMPT = AnalysisPrompt(system="system rules + schema", user="the incident JSON", data={})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("oncall_agent.integrations.http.time.sleep", lambda s: None)


def provider(server: FakeServer, **overrides) -> GroqProvider:
    settings = AISettings(**{"provider": "groq", "groq_api_key": "gsk_" + "k" * 40, "groq_model": "openai/gpt-oss-120b",
                             "groq_base_url": "https://api.groq.test/openai/v1", "max_tokens": 2048, **overrides})  # fmt: skip
    return GroqProvider(settings, transport=server.transport())


def completion(content, finish: str = "stop") -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"total_tokens": 900}}  # fmt: skip


def test_request_uses_json_mode_temperature_zero_and_bearer_key():
    server = FakeServer().on("POST", PATH, (200, completion('{"ok": true}')))
    p = provider(server)
    assert p.complete(PROMPT) == '{"ok": true}'
    req = server.requests[0]
    assert req.headers["Authorization"] == "Bearer gsk_" + "k" * 40
    body = server.body(req)
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0 and body["max_completion_tokens"] == 2048
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "system", "content": "system rules + schema"}, {"role": "user", "content": "the incident JSON"}]
    assert isinstance(p, AIProvider) and p.name == "groq" and p.model == "openai/gpt-oss-120b"


@pytest.mark.parametrize(
    "reply,match",
    [
        ((200, completion('{"partial": ', finish="length")), "cut off"),
        ((200, completion("")), "empty"),
        ((200, completion(None)), "empty"),
        ((200, {"choices": []}), "no choices"),
        ((200, {"error": "x"}), "no choices"),
    ],
)
def test_unusable_completions_raise_provider_errors(reply, match):
    with pytest.raises(AIProviderError, match=match):
        provider(FakeServer().on("POST", PATH, reply)).complete(PROMPT)


def test_invalid_key_is_not_retried():
    server = FakeServer().on("POST", PATH, (401, {"error": {"message": "Invalid API Key"}}))
    with pytest.raises(AIProviderError, match="401"):
        provider(server).complete(PROMPT)
    assert len(server.requests) == 1


def test_rate_limit_is_retried_once():
    server = FakeServer().on("POST", PATH, httpx.Response(429, headers={"Retry-After": "1"}), (200, completion("{}")))
    assert provider(server).complete(PROMPT) == "{}"
    assert len(server.requests) == 2


def test_timeout_raises_provider_error_after_one_retry():  # "AI provider timeout"
    server = FakeServer().on("POST", PATH, raise_timeout)
    with pytest.raises(AIProviderError, match="timed out"):
        provider(server).complete(PROMPT)
    assert len(server.requests) == 2


def test_check_connection_verifies_model_availability():
    models = {"object": "list", "data": [{"id": "openai/gpt-oss-120b"}, {"id": "llama-3.3-70b-versatile"}]}
    assert provider(FakeServer().on("GET", "/openai/v1/models", (200, models))).check_connection() == {"provider": "groq", "model": "openai/gpt-oss-120b"}
    with pytest.raises(AIProviderError, match="not available"):
        provider(FakeServer().on("GET", "/openai/v1/models", (200, models)), groq_model="retired-model").check_connection()
    with pytest.raises(AIProviderError, match="401"):
        provider(FakeServer().on("GET", "/openai/v1/models", (401, {"error": {"message": "Invalid API Key"}}))).check_connection()


def test_missing_configuration_is_rejected():
    with pytest.raises(AIProviderError):
        GroqProvider(AISettings(provider="groq", groq_api_key=None, groq_model="m"))
