"""Shared test fixtures: a fake bridge, a scripted fake model and a fixed clock.

Nothing in the test suite talks to Google, Ollama or Anthropic.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import Container
from app.errors import BridgeError
from app.main import create_app
from app.services.common import ServiceContext
from app.sessions import SessionStore

# Monday 9 November 2026, 08:00 UTC
FIXED_NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "ollama",
        "ollama_model": "test-model",
        "anthropic_api_key": "test-key",
        "claude_model": "test-claude-model",
        "apps_script_url": "https://script.google.com/macros/s/test/exec",
        "apps_script_shared_secret": "test-secret",
        "user_timezone": "UTC",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeBridge:
    """Records every call. Handlers are plain functions or values keyed by operation name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, Any] = {"ping": {"account": "me@example.com"}}

    def on(self, operation: str, handler: Any) -> None:
        self.handlers[operation] = handler

    def call(self, operation: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        self.calls.append((operation, params))
        if operation not in self.handlers:
            raise BridgeError(f"FakeBridge has no handler for {operation}")
        handler = self.handlers[operation]
        if isinstance(handler, Exception):
            raise handler
        return handler(params) if callable(handler) else handler

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_block(tool_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def response(*blocks: dict[str, Any], stop_reason: str | None = None) -> SimpleNamespace:
    if stop_reason is None:
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeClaude:
    """Returns scripted responses in order and keeps a copy of each request's messages."""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script: list[Any] = list(script or [])
        self.requests: list[list[dict[str, Any]]] = []

    def create(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Any:
        self.requests.append([dict(m) for m in messages])
        if not self.script:
            raise AssertionError("FakeClaude ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def claude() -> FakeClaude:
    return FakeClaude()


@pytest.fixture
def clock() -> dict[str, datetime]:
    """Mutable clock: tests can move time forward with clock['now'] = ..."""
    return {"now": FIXED_NOW}


@pytest.fixture
def now_fn(clock: dict[str, datetime]) -> Callable[[], datetime]:
    return lambda: clock["now"]


@pytest.fixture
def ctx(settings: Settings, bridge: FakeBridge, now_fn: Callable[[], datetime]) -> ServiceContext:
    return ServiceContext(settings=settings, bridge=bridge, now=now_fn)


@pytest.fixture
def container(settings, bridge, claude, now_fn) -> Container:
    return Container(
        settings=settings,
        bridge=bridge,
        model=claude,
        sessions=SessionStore(settings.session_idle_ttl_seconds, now_fn),
        now=now_fn,
    )


def make_client(app, *, host: str = "localhost", client_ip: str = "127.0.0.1", **kwargs: Any) -> TestClient:
    """TestClient whose requests appear to come from client_ip and be addressed to host."""

    async def with_client_address(scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(client_ip, 50000))
        await app(scope, receive, send)

    return TestClient(with_client_address, base_url=f"http://{host}", **kwargs)


@pytest.fixture
def client(container: Container) -> TestClient:
    return make_client(create_app(container))
