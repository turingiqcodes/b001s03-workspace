"""In-memory GitHub for offline development and tests."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...exceptions import GitHubError
from ..github import CommitDetail, CommitSummary
from .base import FaultInjector
from .sample_data import build_github_sample


class MockGitHubClient(FaultInjector):
    error_cls = GitHubError

    def __init__(self, commits: list[CommitDetail] | None = None, repository: str = "example-org/airflow-dags") -> None:
        super().__init__()
        self.commits = list(commits) if commits is not None else build_github_sample()
        self.repository = repository

    def close(self) -> None:
        pass

    def check_connection(self) -> dict[str, Any]:
        self._enter("check_connection")
        return {"repository": self.repository, "private": True}

    def list_commits(self, *, path: str | None = None, since: datetime | None = None, max_items: int = 10) -> list[CommitSummary]:
        self._enter("list_commits", path=path)
        result = [
            c for c in sorted(self.commits, key=lambda c: c.committed_at or datetime.min, reverse=True)
            if (path is None or any(f.filename == path or f.filename.startswith(path.rstrip("/") + "/") for f in c.files))
            and (since is None or (c.committed_at is not None and c.committed_at >= since))
        ]  # fmt: skip
        return [CommitSummary(**{k: v for k, v in c.model_dump().items() if k != "files"}) for c in result[:max_items]]

    def get_commit(self, sha: str) -> CommitDetail:
        self._enter("get_commit", sha=sha)
        for c in self.commits:
            if c.sha.startswith(sha.lower()):
                return c
        raise GitHubError(f"Commit {sha} not found", status_code=404)
