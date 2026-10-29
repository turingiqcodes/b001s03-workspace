"""Server assembly and transport runners (stdio and streamable HTTP)."""

from __future__ import annotations

import logging

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import Settings
from .state import AppState
from .tools import register_all

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Tools for the GitHub REST API: repositories, pull requests, Actions CI/CD, issues, and search. "
    "Write tools accept dry_run=true to preview a change without performing it. "
    "Pending reviews: create_pull_request_review without an event, then submit_pull_request_review."
)


def build_server(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[MCPServer, AppState]:
    """Create the MCP server with all tools registered.

    ``transport`` is passed to every outgoing httpx client (used by tests to mock GitHub).
    """
    app_state = AppState(settings, transport=transport)
    mcp = MCPServer(
        name="github-mcp-server",
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,  # type: ignore[arg-type]
    )
    register_all(mcp, app_state)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:  # no credentials or MCP session required
        return JSONResponse({"status": "ok"})

    return mcp, app_state


def build_http_app(mcp: MCPServer, settings: Settings) -> Starlette:
    """Starlette app serving MCP at /mcp and GET /health."""
    return mcp.streamable_http_app(streamable_http_path="/mcp", host=settings.host)


async def run_stdio(settings: Settings) -> None:
    mcp, app_state = build_server(settings)
    logger.info("Starting GitHub MCP server on stdio (auth=%s, dry_run=%s)", settings.auth_method, settings.dry_run)
    try:
        await mcp.run_stdio_async()
    finally:
        await app_state.aclose()


async def run_http(settings: Settings) -> None:
    mcp, app_state = build_server(settings)
    app = build_http_app(mcp, settings)
    logger.info(
        "Starting GitHub MCP server on http://%s:%d/mcp (auth=%s, per_request_auth=%s, dry_run=%s)",
        settings.host, settings.port, settings.auth_method, settings.per_request_auth, settings.dry_run,
    )
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
    try:
        await uvicorn.Server(config).serve()
    finally:
        await app_state.aclose()
