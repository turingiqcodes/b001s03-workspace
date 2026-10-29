"""Transports: health endpoint, streamable HTTP with per-request tokens, stdio, and CLI startup."""

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from github_mcp.server import build_http_app, build_server
from github_mcp.state import AppState
from github_mcp.tools import ALL_TOOLS

from .conftest import MockGitHub, error_text, make_settings

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_http_server(settings, gh: MockGitHub):
    mcp, state = build_server(settings, transport=gh.transport)
    app = build_http_app(mcp, settings)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    try:
        yield f"http://{settings.host}:{settings.port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ------------------------------------------------------------------ health
def test_health_without_credentials_or_session():
    settings = make_settings(token=None, transport="http", per_request_auth=True)
    mcp, _ = build_server(settings)
    with TestClient(build_http_app(mcp, settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


# ------------------------------------------------------------------ per-request token isolation (unit)
async def test_per_request_client_is_isolated_and_closed():
    gh = MockGitHub()
    state = AppState(make_settings(transport="http"), transport=gh.transport)
    shared = state.shared_client
    async with state.client_for({"x-github-token": "ghp_request_A"}) as client:
        assert client is not shared
        per_request_http = client.http
    assert per_request_http.is_closed
    assert not shared.http.is_closed
    assert "Authorization" not in shared.http.headers
    await state.aclose()


async def test_header_ignored_outside_http_mode():
    state = AppState(make_settings(transport="stdio"))
    async with state.client_for({"x-github-token": "ghp_x"}) as client:
        assert client is state.shared_client
    await state.aclose()


async def test_per_request_client_closed_even_when_call_fails():
    state = AppState(make_settings(transport="http"))
    with pytest.raises(RuntimeError):
        async with state.client_for({"x-github-token": "t"}) as client:
            http = client.http
            raise RuntimeError("tool failed")
    assert http.is_closed


# ------------------------------------------------------------------ streamable HTTP end to end
async def _call_over_http(base_url: str, token: str | None, tool: str, args: dict):
    headers = {"X-GitHub-Token": token} if token else {}
    async with httpx.AsyncClient(headers=headers) as http:
        async with Client(streamable_http_client(base_url + "/mcp", http_client=http)) as client:
            return await client.call_tool(tool, args)


async def test_http_transport_lists_tools_and_serves_health():
    gh = MockGitHub()
    settings = make_settings(transport="http", port=free_port())
    with running_http_server(settings, gh) as base:
        async with httpx.AsyncClient() as http:
            assert (await http.get(base + "/health")).json() == {"status": "ok"}
            async with Client(streamable_http_client(base + "/mcp", http_client=http)) as client:
                names = {t.name for t in (await client.list_tools()).tools}
    assert names == set(ALL_TOOLS)


async def test_http_per_request_tokens_never_leak_between_requests():
    gh = MockGitHub()
    gh.add("GET", "/repos/o/r", lambda req: httpx.Response(200, json={"auth": req.headers["Authorization"]}))
    settings = make_settings(token="ghp_server_token", transport="http", port=free_port())
    with running_http_server(settings, gh) as base:
        tokens = [f"ghp_user_{i}" for i in range(6)] + [None, None]
        results = await asyncio.gather(*(
            _call_over_http(base, t, "get_repository", {"owner": "o", "repo": "r"}) for t in tokens))
    for token, result in zip(tokens, results):
        expected = f"Bearer {token or 'ghp_server_token'}"
        assert result.structured_content["auth"] == expected


async def test_http_per_request_auth_mode_without_server_credentials():
    gh = MockGitHub()
    gh.add("GET", "/repos/o/r", lambda req: httpx.Response(200, json={"auth": req.headers["Authorization"]}))
    settings = make_settings(token=None, transport="http", per_request_auth=True, port=free_port())
    with running_http_server(settings, gh) as base:
        ok = await _call_over_http(base, "ghp_only_this_request", "get_repository", {"owner": "o", "repo": "r"})
        missing = await _call_over_http(base, None, "get_repository", {"owner": "o", "repo": "r"})
    assert ok.structured_content["auth"] == "Bearer ghp_only_this_request"
    assert "X-GitHub-Token" in error_text(missing)


# ------------------------------------------------------------------ stdio and CLI (real subprocesses)
def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "MCP_"))}
    env["PYTHONPATH"] = SRC_DIR
    env.update(extra)
    return env


async def test_stdio_transport_subprocess(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "github_mcp", "--transport", "stdio"],
        env=_env(GITHUB_TOKEN="ghp_fake_for_listing", GITHUB_DRY_RUN="true"), cwd=str(tmp_path))
    async with Client(params) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        preview = await client.call_tool("close_issue", {"owner": "o", "repo": "r", "issue_number": 1})
    assert names == set(ALL_TOOLS)
    assert preview.structured_content["status"] == "dry_run"


def test_cli_fails_fast_without_credentials(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "github_mcp", "--transport", "stdio"], env=_env(),
                          cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert "Configuration error: No GitHub credentials configured" in proc.stderr


def test_cli_check_config_per_request_http(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "github_mcp", "--transport", "http", "--check-config"],
                          env=_env(GITHUB_PER_REQUEST_AUTH="true"), cwd=tmp_path, capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0 and "Configuration OK (transport=http, auth=none" in proc.stderr


def test_cli_http_server_starts_and_health_responds(tmp_path):
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "github_mcp", "--transport", "http", "--port", str(port)],
                            env=_env(GITHUB_TOKEN="ghp_fake"), cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 20
        while True:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except httpx.TransportError:
                if time.time() > deadline or proc.poll() is not None:
                    raise AssertionError(f"server did not start: {proc.stderr.read() if proc.poll() is not None else ''}")
                time.sleep(0.2)
        assert response.json() == {"status": "ok"}
    finally:
        proc.terminate()
        _, stderr = proc.communicate(timeout=15)
    assert "ghp_fake" not in stderr  # credentials never logged
