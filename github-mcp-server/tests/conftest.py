"""Shared test fixtures. Every GitHub call goes through an httpx.MockTransport: no network, no real tokens."""

from __future__ import annotations

import json as jsonlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client

from github_mcp.config import Settings
from github_mcp.server import build_server

TEST_TOKEN = "ghp_test_server_token"


@dataclass
class Route:
    method: str
    path: str
    responses: list[Any]
    calls: int = 0


@dataclass
class MockGitHub:
    """Minimal programmable fake of the GitHub REST API."""

    routes: list[Route] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, method: str, path: str, *responses: Any) -> Route:
        """Register responses for METHOD PATH. Each response is an httpx.Response, a JSON-able
        object (200), an Exception to raise, or a callable(request) -> httpx.Response.
        The last response repeats once the sequence is exhausted."""
        route = Route(method.upper(), path, list(responses) or [{}])
        self.routes.append(route)
        return route

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for route in self.routes:
            if route.method == request.method and route.path == request.url.path:
                item = route.responses[min(route.calls, len(route.responses) - 1)]
                route.calls += 1
                if isinstance(item, Exception):
                    raise item
                if callable(item):
                    return item(request)
                if isinstance(item, httpx.Response):
                    return item
                return httpx.Response(200, json=item)
        return httpx.Response(404, json={"message": f"unmocked {request.method} {request.url.path}"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def methods(self) -> list[str]:
        return [r.method for r in self.requests]

    def body(self, index: int = -1) -> Any:
        return jsonlib.loads(self.requests[index].content)


@pytest.fixture
def gh() -> MockGitHub:
    return MockGitHub()


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"token": TEST_TOKEN, "max_retries": 3, "max_rate_limit_wait": 60.0}
    base.update(overrides)
    return Settings(**base)


@asynccontextmanager
async def connect(gh: MockGitHub, **settings_overrides: Any) -> AsyncIterator[Client]:
    """In-process MCP client connected to a fresh server backed by ``gh``."""
    mcp, state = build_server(make_settings(**settings_overrides), transport=gh.transport)
    try:
        async with Client(mcp) as client:
            yield client
    finally:
        await state.aclose()


async def call(gh: MockGitHub, tool: str, args: dict[str, Any], **settings_overrides: Any):
    async with connect(gh, **settings_overrides) as client:
        return await client.call_tool(tool, args)


def error_text(result) -> str:
    assert result.is_error, f"expected an error result, got {result.structured_content}"
    return result.content[0].text


@pytest.fixture(scope="session")
def rsa_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def no_sleep() -> Callable[..., Any]:
    """Records requested sleeps instead of sleeping."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    fake_sleep.calls = slept  # type: ignore[attr-defined]
    return fake_sleep
