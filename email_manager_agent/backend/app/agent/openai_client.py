"""Client for OpenAI-compatible chat APIs (POST {base}/chat/completions with tools).

Used when LLM_PROVIDER=openai. The default base URL is Groq's free-tier API; the same
client also works with OpenAI or any other service that speaks this format.

Like OllamaClient, it converts the agent loop's Claude-format history (text, tool_use and
tool_result blocks) to the provider's messages on the way out, and the reply back into
the same block format, so the agent loop works the same with every provider.

OpenAI-format differences handled here:
- tool call arguments are a JSON string, not an object;
- tool results are separate messages with role "tool" and the matching tool_call_id;
- an assistant message that only calls tools has content null.
"""
import json
import logging
import time
import uuid
from types import SimpleNamespace
from typing import Any, Callable

import httpx

from app.agent.ollama_client import _blocks, _get, _parse_arguments, to_ollama_tools
from app.config import Settings
from app.errors import ConfigurationError, ModelError

logger = logging.getLogger(__name__)

# Free tiers (e.g. Groq) have per-minute token limits and say how long to wait. Wait and
# retry when that is short; otherwise report the rate limit to the user.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_MAX_WAIT_SECONDS = 30.0

# Ollama and OpenAI use the same tool definition shape.
to_openai_tools = to_ollama_tools


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Claude-format history to OpenAI chat messages."""
    converted: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for message in messages:
        blocks = _blocks(message.get("content"))
        text = "\n".join(_get(b, "text", "") for b in blocks if _get(b, "type") == "text").strip()
        if message.get("role") == "assistant":
            calls = [
                {
                    "id": str(_get(block, "id")),
                    "type": "function",
                    "function": {
                        "name": _get(block, "name"),
                        "arguments": json.dumps(_get(block, "input") or {}),
                    },
                }
                for block in blocks
                if _get(block, "type") == "tool_use"
            ]
            out: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                out["tool_calls"] = calls
            elif out["content"] is None:
                out["content"] = ""
            converted.append(out)
            continue

        # User turn: tool results first (each its own "tool" message), then any text.
        for block in blocks:
            if _get(block, "type") == "tool_result":
                content = str(_get(block, "content", ""))
                if _get(block, "is_error"):
                    content = "ERROR: " + content
                converted.append(
                    {"role": "tool", "tool_call_id": str(_get(block, "tool_use_id")), "content": content}
                )
        if text:
            converted.append({"role": "user", "content": text})
    return converted


def from_openai_response(data: Any) -> SimpleNamespace:
    """Convert a chat/completions reply into Claude-style content blocks and a stop_reason."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
        raise ModelError("The model API returned an unexpected response.")
    choice = choices[0]
    message = choice["message"]

    content: list[dict[str, Any]] = []
    text = str(message.get("content") or "").strip()
    if text:
        content.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        content.append({
            "type": "tool_use",
            "id": str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
            "name": str(function.get("name", "")),
            "input": _parse_arguments(function.get("arguments")),
        })

    if any(block["type"] == "tool_use" for block in content):
        stop_reason = "tool_use"
    elif choice.get("finish_reason") == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings
        self._http = httpx.Client(timeout=settings.openai_timeout_seconds, transport=transport)
        self._sleep = sleep

    @property
    def _base_url(self) -> str:
        return self._settings.openai_base_url.rstrip("/")

    def _check_config(self) -> str:
        issues = self._settings.openai_config_issues()
        if issues:
            raise ConfigurationError("The model API is not configured: " + " ".join(issues))
        return self._settings.openai_model.strip()

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"Authorization": "Bearer " + self._settings.openai_api_key.get_secret_value()}
        try:
            return self._http.request(method, self._base_url + path, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelError(
                f"The model API did not answer within {self._settings.openai_timeout_seconds:g} seconds. Try again.",
                http_status=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                f"Could not connect to the model API at {self._base_url}. Check your internet connection "
                "and OPENAI_BASE_URL."
            ) from exc

    def create(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any:
        model = self._check_config()
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(system, messages),
            "max_completion_tokens": self._settings.openai_max_tokens,
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"

        response = self._request("POST", "/chat/completions", body)
        for _ in range(RATE_LIMIT_RETRIES):
            wait = self._retry_wait(response)
            if wait is None:
                break
            logger.info("Model API rate limit: waiting %.1f s before retrying.", wait)
            self._sleep(wait)
            response = self._request("POST", "/chat/completions", body)
        if response.status_code >= 400:
            raise self._error_for(response, model)
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("The model API returned a response that is not JSON.") from exc
        return from_openai_response(data)

    def check(self) -> None:
        """Confirm the key works and the model exists (used by GET /api/status?check_bridge=true)."""
        model = self._check_config()
        response = self._request("GET", "/models")
        if response.status_code >= 400:
            raise self._error_for(response, model)
        try:
            ids = {m.get("id") for m in response.json().get("data", [])}
        except (ValueError, AttributeError) as exc:
            raise ModelError("The model API returned an unexpected model list.") from exc
        if model not in ids:
            raise ConfigurationError(
                f"Model '{model}' is not available for this API key. Check OPENAI_MODEL in .env "
                "(Groq's list: https://console.groq.com/docs/models)."
            )

    @staticmethod
    def _retry_wait(response: httpx.Response) -> float | None:
        """Seconds to wait before retrying a 429, or None if it should not be retried."""
        if response.status_code != 429:
            return None
        try:
            wait = float(response.headers.get("retry-after", ""))
        except ValueError:
            return None
        if wait < 0 or wait > RATE_LIMIT_MAX_WAIT_SECONDS:
            return None
        return wait + 0.5

    @staticmethod
    def _error_for(response: httpx.Response, model: str) -> Exception:
        try:
            error = response.json().get("error") or {}
            if not isinstance(error, dict):
                error = {"message": str(error)}
        except (ValueError, AttributeError):
            error = {"message": response.text}
        detail = str(error.get("message", ""))[:300]
        code = str(error.get("code") or "")
        status = response.status_code
        logger.warning("Model API error %s %s: %s", status, code, detail)

        if status == 401:
            return ConfigurationError("The model API rejected OPENAI_API_KEY. Check the key in .env.")
        if status == 404 or code == "model_not_found":
            return ConfigurationError(f"Model '{model}' was not found. Check OPENAI_MODEL in .env.")
        if status == 429:
            return ModelError(
                "The model API is rate limiting requests (free-tier limit). Wait a minute and try again.",
                http_status=429,
            )
        if status == 413 or code == "request_too_large":
            return ModelError(
                "The request is too large for the model's limits. Ask about fewer emails at once, or lower "
                "EMAIL_BODY_MAX_CHARS in .env.",
                http_status=429,
            )
        if code == "tool_use_failed":
            return ModelError("The model produced an invalid tool call. Try again or rephrase the request.")
        return ModelError(f"The model API returned HTTP {status}: {detail or 'unknown error'}")
