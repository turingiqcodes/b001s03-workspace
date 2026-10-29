"""Server-wide state: the shared GitHub client, per-request clients, and dry-run handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping
from urllib.parse import quote

import httpx

from .auth import StaticTokenProvider, build_token_provider
from .client import GitHubClient, new_http_client
from .config import Settings
from .errors import AuthenticationError

logger = logging.getLogger(__name__)

TOKEN_HEADER = "x-github-token"


class AppState:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """``transport`` lets tests inject an ``httpx.MockTransport`` for every outgoing call."""
        self.settings = settings
        self._transport = transport
        self._shared: GitHubClient | None = None

    @property
    def shared_client(self) -> GitHubClient | None:
        """Lazily created client for the server's own credentials (None if none configured)."""
        if self._shared is None and self.settings.has_server_credentials:
            http = new_http_client(self.settings, self._transport)
            self._shared = GitHubClient(self.settings, build_token_provider(self.settings, http), http)
        return self._shared

    async def aclose(self) -> None:
        if self._shared is not None:
            await self._shared.aclose()
            self._shared = None

    @asynccontextmanager
    async def client_for(self, headers: Mapping[str, str] | None) -> AsyncIterator[GitHubClient]:
        """Yield the client to use for one tool call.

        In HTTP mode an ``X-GitHub-Token`` header gets a brand-new client with its
        own connection pool, closed when the call ends, so that token never touches
        the shared client or any other request.
        """
        token = None
        if headers is not None and self.settings.transport == "http":
            token = (headers.get(TOKEN_HEADER) or "").strip() or None
        if token:
            client = GitHubClient(
                self.settings, StaticTokenProvider(token), new_http_client(self.settings, self._transport)
            )
            try:
                yield client
            finally:
                await client.aclose()
            return
        shared = self.shared_client
        if shared is None:
            raise AuthenticationError(
                "No GitHub credentials for this request: send an X-GitHub-Token header "
                "(the server runs in per-request authentication mode)."
            )
        yield shared

    def is_dry_run(self, requested: bool) -> bool:
        return self.settings.dry_run or requested


def seg(value: str | int) -> str:
    """Encode one URL path segment (owner, repo, ref, ...)."""
    return quote(str(value), safe="")


def repo_path(owner: str, repo: str, *rest: str | int) -> str:
    return "/".join(["repos", seg(owner), seg(repo), *(str(r) for r in rest)])


def encode_file_path(path: str) -> str:
    """Encode a repository file path, keeping ``/`` and rejecting traversal segments."""
    cleaned = path.strip().strip("/")
    if any(part in {"..", "."} for part in cleaned.split("/")):
        raise ValueError("path must not contain '.' or '..' segments")
    return quote(cleaned, safe="/")


def dry_run_preview(action: str, method: str, path: str, body: Any = None, **notes: Any) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "dry_run": True,
        "action": action,
        "request": {"method": method, "path": "/" + path.lstrip("/"), "body": body},
        "message": "DRY RUN: no request was sent and nothing was changed on GitHub.",
        **notes,
    }


def completed(action: str, result: Any, **extra: Any) -> dict[str, Any]:
    return {"status": "completed", "dry_run": False, "action": action, "result": result, **extra}
