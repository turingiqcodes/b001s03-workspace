"""GitHub REST API client, read-only (FR-025/026, NFR-010/013/015).

Only two read operations are needed: list recent commits touching a path, and fetch
one commit with its file patches. The token needs read-only "Contents" access.
Pagination follows the ``Link`` header (NFR-015) and stops at ``max_items``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config import GitHubSettings
from ..exceptions import GitHubError, MalformedResponseError
from .http import json_body, send

log = logging.getLogger(__name__)

SERVICE = "github"
API_URL = "https://api.github.com"
MAX_PAGES = 20
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class CommitSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str = Field(min_length=7)
    message: str
    author_name: str | None = None
    author_email: str | None = None
    author_login: str | None = None
    committed_at: datetime | None = None
    html_url: str | None = None

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def title(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


class FileChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    # GitHub omits the patch for binary and very large diffs; that is "missing
    # evidence", not "no change" (FR-029).
    patch: str | None = None


class CommitDetail(CommitSummary):
    files: list[FileChange] = Field(default_factory=list)


@runtime_checkable
class GitHubClient(Protocol):
    def check_connection(self) -> dict[str, Any]: ...
    def list_commits(self, *, path: str | None = None, since: datetime | None = None, max_items: int = 10) -> list[CommitSummary]: ...
    def get_commit(self, sha: str) -> CommitDetail: ...
    def close(self) -> None: ...


def _summary_from(item: Any) -> CommitSummary:
    if not isinstance(item, dict) or not isinstance(item.get("sha"), str) or not isinstance(item.get("commit"), dict):
        raise MalformedResponseError(SERVICE, "commit entry missing 'sha' or 'commit'")
    commit = item["commit"]
    author = commit.get("author") or {}
    login = (item.get("author") or {}).get("login") if isinstance(item.get("author"), dict) else None
    try:
        return CommitSummary(
            sha=item["sha"],
            message=str(commit.get("message") or ""),
            author_name=author.get("name"),
            author_email=author.get("email"),
            author_login=login,
            committed_at=author.get("date"),
            html_url=item.get("html_url"),
        )
    except ValueError as exc:
        raise MalformedResponseError(SERVICE, f"invalid commit entry: {exc}") from None


def _is_rate_limited(response: httpx.Response) -> bool:
    # GitHub signals primary rate limits with 403 + x-ratelimit-remaining: 0.
    return response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"


class HttpGitHubClient:
    def __init__(self, settings: GitHubSettings, *, transport: httpx.BaseTransport | None = None, base_url: str = API_URL) -> None:
        if settings.token is None or not settings.repository:
            raise GitHubError("GITHUB_TOKEN and GITHUB_REPOSITORY must be configured")
        self._settings = settings
        self._repo = settings.repository
        kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": settings.timeout_seconds,
            "headers": {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {settings.token.get_secret_value()}",
                "User-Agent": "airflow-oncall-agent/1.0",
            },
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return send(self._client, "GET", url, error_cls=GitHubError, params=params, is_transient=_is_rate_limited)

    def check_connection(self) -> dict[str, Any]:
        body = json_body(self._get(f"/repos/{self._repo}"), SERVICE)
        if not isinstance(body, dict) or "full_name" not in body:
            raise MalformedResponseError(SERVICE, "repository response missing 'full_name'")
        return {"repository": body["full_name"], "private": body.get("private")}

    def list_commits(self, *, path: str | None = None, since: datetime | None = None, max_items: int = 10) -> list[CommitSummary]:
        params: dict[str, Any] = {"per_page": min(max(max_items, 1), 100)}
        if path:
            params["path"] = path
        if since:
            params["since"] = (since if since.tzinfo else since.replace(tzinfo=timezone.utc)).isoformat()
        commits: list[CommitSummary] = []
        url: str | None = f"/repos/{self._repo}/commits"
        for _ in range(MAX_PAGES):
            if url is None or len(commits) >= max_items:
                break
            response = self._get(url, params)
            body = json_body(response, SERVICE)
            if not isinstance(body, list):
                raise MalformedResponseError(SERVICE, "commit list is not a list")
            commits.extend(_summary_from(item) for item in body)
            match = _NEXT_LINK.search(response.headers.get("link", ""))
            url, params = (match.group(1), None) if match else (None, None)
        return commits[:max_items]

    def get_commit(self, sha: str) -> CommitDetail:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            raise GitHubError(f"Invalid commit sha {sha!r}")
        body = json_body(self._get(f"/repos/{self._repo}/commits/{quote(sha)}"), SERVICE)
        summary = _summary_from(body)
        files_raw = body.get("files", [])
        if not isinstance(files_raw, list):
            raise MalformedResponseError(SERVICE, "commit 'files' is not a list")
        try:
            files = [FileChange.model_validate(f) for f in files_raw if isinstance(f, dict)]
        except ValueError as exc:
            raise MalformedResponseError(SERVICE, f"invalid file entry: {exc}") from None
        return CommitDetail(**summary.model_dump(), files=files)
