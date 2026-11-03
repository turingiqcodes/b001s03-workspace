"""Client for a local Ollama server (POST /api/chat with tools).

The agent loop keeps its history in the Claude Messages format (content blocks such as
text, tool_use and tool_result). This client converts that history to Ollama's chat
format on the way out, and converts Ollama's reply back into the same block format, so
the agent loop works the same with either provider.

Ollama differences handled here:
- tool calls have no id, so one is generated and remembered to match the tool results;
- tool results are separate messages with role "tool" and the tool's name;
- the context window (num_ctx) must be set, because Ollama's default is too small
  for 21 tool definitions plus email content.
"""
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import httpx

from app.config import Settings
from app.errors import ConfigurationError, ModelError

logger = logging.getLogger(__name__)


def _get(block: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dict block or an object block."""
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _blocks(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content or [])


def to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in tools
    ]


def to_ollama_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Claude-format history to Ollama chat messages."""
    converted: list[dict[str, Any]] = [{"role": "system", "content": system}]
    tool_names: dict[str, str] = {}  # tool_use id -> tool name, for the matching results

    for message in messages:
        blocks = _blocks(message.get("content"))
        if message.get("role") == "assistant":
            text = "\n".join(_get(b, "text", "") for b in blocks if _get(b, "type") == "text").strip()
            calls = []
            for block in blocks:
                if _get(block, "type") == "tool_use":
                    tool_names[_get(block, "id")] = _get(block, "name")
                    calls.append({"function": {"name": _get(block, "name"), "arguments": _get(block, "input") or {}}})
            out: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                out["tool_calls"] = calls
            converted.append(out)
            continue

        # User turn: tool results first (each its own "tool" message), then any text.
        for block in blocks:
            if _get(block, "type") == "tool_result":
                content = str(_get(block, "content", ""))
                if _get(block, "is_error"):
                    content = "ERROR: " + content
                converted.append(
                    {"role": "tool", "tool_name": tool_names.get(_get(block, "tool_use_id"), ""), "content": content}
                )
        text = "\n".join(_get(b, "text", "") for b in blocks if _get(b, "type") == "text").strip()
        if text:
            converted.append({"role": "user", "content": text})
    return converted


def _parse_arguments(arguments: Any) -> Any:
    """Ollama usually returns an object; some models return a JSON string instead."""
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return arguments  # the agent loop reports it to the model as invalid arguments
    return arguments


def from_ollama_response(data: Any) -> SimpleNamespace:
    """Convert an Ollama /api/chat reply into Claude-style content blocks and a stop_reason."""
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, dict):
        raise ModelError("Ollama returned an unexpected response.")

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
    elif data.get("done_reason") == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class OllamaClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._http = httpx.Client(timeout=settings.ollama_timeout_seconds, transport=transport)

    @property
    def _base_url(self) -> str:
        return self._settings.ollama_base_url.rstrip("/")

    def _check_config(self) -> str:
        issues = self._settings.ollama_config_issues()
        if issues:
            raise ConfigurationError("Ollama is not configured: " + " ".join(issues))
        return self._settings.ollama_model.strip()

    def _post(self, path: str, body: dict[str, Any] | None = None, method: str = "POST") -> httpx.Response:
        url = self._base_url + path
        try:
            return self._http.request(method, url, json=body)
        except httpx.ConnectError as exc:
            raise ConfigurationError(
                f"Cannot connect to Ollama at {self._base_url}. Start it with 'ollama serve' (or open the Ollama app)."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ModelError(
                f"The local model did not answer within {self._settings.ollama_timeout_seconds:g} seconds. "
                "Try a smaller model or raise OLLAMA_TIMEOUT_SECONDS.",
                http_status=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"Could not talk to Ollama: {exc.__class__.__name__}.") from exc

    def create(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any:
        model = self._check_config()
        body: dict[str, Any] = {
            "model": model,
            "messages": to_ollama_messages(system, messages),
            "tools": to_ollama_tools(tools),
            "stream": False,
            "options": {"num_ctx": self._settings.ollama_num_ctx},
            "keep_alive": self._settings.ollama_keep_alive,
        }
        if self._settings.ollama_think is not None:
            body["think"] = self._settings.ollama_think

        response = self._post("/api/chat", body)
        if response.status_code >= 400:
            raise self._error_for(response, model)
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("Ollama returned a response that is not JSON.") from exc
        return from_ollama_response(data)

    def check(self) -> None:
        """Confirm the server is up and the model is pulled (used by GET /api/status?check_bridge=true)."""
        model = self._check_config()
        response = self._post("/api/tags", method="GET")
        if response.status_code >= 400:
            raise ModelError(f"Ollama returned HTTP {response.status_code} when listing models.")
        try:
            names = {m.get("name") for m in response.json().get("models", [])}
        except (ValueError, AttributeError) as exc:
            raise ModelError("Ollama returned an unexpected model list.") from exc
        if model not in names and f"{model}:latest" not in names:
            raise ConfigurationError(f"Ollama model '{model}' is not installed. Run: ollama pull {model}")

    @staticmethod
    def _error_for(response: httpx.Response, model: str) -> Exception:
        try:
            detail = str(response.json().get("error", ""))
        except (ValueError, AttributeError):
            detail = response.text
        detail = detail[:300]
        logger.warning("Ollama error %s: %s", response.status_code, detail)
        if response.status_code == 404 or "not found" in detail.lower():
            return ConfigurationError(f"Ollama model '{model}' is not installed. Run: ollama pull {model}")
        if "does not support tools" in detail.lower():
            return ConfigurationError(
                f"Ollama model '{model}' does not support tool calling. Use a tool-capable model such as qwen3:8b or llama3.1:8b."
            )
        if "does not support thinking" in detail.lower():
            return ConfigurationError(f"Ollama model '{model}' does not support thinking. Remove OLLAMA_THINK from .env.")
        return ModelError(f"Ollama returned HTTP {response.status_code}: {detail or 'unknown error'}")
