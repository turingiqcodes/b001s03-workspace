"""Thin wrapper around the Anthropic SDK.

It reads the model and limits from configuration and turns SDK exceptions into
readable AppErrors. The agent loop only ever calls `create()`.
"""
import logging
from typing import Any, Protocol

import anthropic

from app.config import Settings
from app.errors import ConfigurationError, ModelError

logger = logging.getLogger(__name__)

# Opt-in server-side fallback: if the model declines a request, the API retries it on a
# suitable fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class MessageCreator(Protocol):
    """Anything that answers like the Claude Messages API. OllamaClient converts to this shape."""

    def create(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any: ...


class ClaudeClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        issues = self._settings.llm_config_issues()
        if issues:
            raise ConfigurationError("Claude is not configured: " + " ".join(issues))
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(),
                timeout=self._settings.claude_timeout_seconds,
                max_retries=2,
            )
        return self._client

    def create(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any:
        client = self._get_client()
        model = self._settings.claude_model.strip()
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": self._settings.claude_max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._settings.claude_effort},
            "cache_control": {"type": "ephemeral"},  # cache the stable tools + system + history prefix
        }
        try:
            if self._settings.claude_refusal_fallback:
                return client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
            return client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise ConfigurationError("Claude rejected ANTHROPIC_API_KEY. Check the key in .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ConfigurationError("The Claude API key does not have permission for this request.") from exc
        except anthropic.NotFoundError as exc:
            raise ConfigurationError(
                f"Claude model '{model}' was not found. Check CLAUDE_MODEL in .env."
            ) from exc
        except anthropic.BadRequestError as exc:
            logger.warning("Claude bad request: %s", exc.message)
            raise ModelError(
                f"Claude rejected the request: {exc.message}. If this mentions the model, thinking, effort or "
                "fallbacks, check CLAUDE_MODEL, CLAUDE_EFFORT and CLAUDE_REFUSAL_FALLBACK in .env."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("Claude is rate limiting requests. Wait a minute and try again.", http_status=429) from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"Claude is temporarily unavailable (HTTP {exc.status_code}). Try again shortly.") from exc
        except anthropic.APITimeoutError as exc:
            raise ModelError("Claude took too long to answer. Try again.", http_status=504) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError("Could not connect to Claude. Check your internet connection.") from exc
