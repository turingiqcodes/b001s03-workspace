"""Real GitHub client against recorded REST responses (FR-025/026, NFR-015; "GitHub unavailable")."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from oncall_agent.config import GitHubSettings
from oncall_agent.exceptions import GitHubError, MalformedResponseError
from oncall_agent.integrations.github import GitHubClient, HttpGitHubClient

from .fakes import FakeServer, raise_connect

pytestmark = pytest.mark.integration

REPO = "example-org/airflow-dags"
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("oncall_agent.integrations.http.time.sleep", lambda s: None)


def client(server: FakeServer) -> HttpGitHubClient:
    return HttpGitHubClient(GitHubSettings(token="ghp_" + "x" * 36, repository=REPO), transport=server.transport(), base_url="https://api.github.test")


def commit(sha: str, msg: str = "Fix", login: str | None = "alice-gh") -> dict:
    return {
        "sha": sha, "html_url": f"https://github.com/{REPO}/commit/{sha}",
        "commit": {"message": msg, "author": {"name": "Alice", "email": "alice@example.com", "date": "2026-11-05T03:00:00Z"}},
        "author": {"login": login} if login else None,
    }  # fmt: skip


def test_check_connection_and_headers():
    server = FakeServer().on("GET", f"/repos/{REPO}", (200, {"full_name": REPO, "private": True}))
    assert client(server).check_connection() == {"repository": REPO, "private": True}
    headers = server.requests[0].headers
    assert headers["Authorization"].startswith("Bearer ghp_") and headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert isinstance(client(server), GitHubClient)


def test_list_commits_filters_by_path_and_since_and_follows_link_pagination():
    server = FakeServer().on(
        "GET", f"/repos/{REPO}/commits",
        httpx.Response(200, json=[commit("1" * 40), commit("2" * 40)], headers={"link": f'<https://api.github.test/repos/{REPO}/commits?page=2&per_page=2>; rel="next"'}),
        httpx.Response(200, json=[commit("3" * 40, login=None)]),
    )  # fmt: skip
    commits = client(server).list_commits(path="dags/customer_sync.py", since=datetime(2026, 11, 1, tzinfo=timezone.utc), max_items=5)
    assert [c.sha[0] for c in commits] == ["1", "2", "3"]
    first = server.requests[0].url.params
    assert first["path"] == "dags/customer_sync.py" and first["since"] == "2026-11-01T00:00:00+00:00"
    assert server.requests[1].url.params["page"] == "2"
    assert commits[0].author_login == "alice-gh" and commits[2].author_login is None
    assert commits[0].author_email == "alice@example.com" and commits[0].title == "Fix"


def test_list_commits_stops_at_max_items():
    server = FakeServer().on("GET", f"/repos/{REPO}/commits", httpx.Response(200, json=[commit("1" * 40), commit("2" * 40)], headers={"link": '<https://api.github.test/next>; rel="next"'}))
    assert len(client(server).list_commits(max_items=2)) == 2
    assert len(server.requests) == 1


def test_get_commit_with_files_and_missing_patch():  # FR-026, FR-029
    body = commit(SHA, "Rename tier") | {"files": [
        {"filename": "dags/customer_sync.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"},
        {"filename": "data/big.bin", "status": "added", "additions": 0, "deletions": 0},
    ]}  # fmt: skip
    server = FakeServer().on("GET", f"/repos/{REPO}/commits/{SHA}", (200, body))
    detail = client(server).get_commit(SHA)
    assert detail.files[0].patch.startswith("@@") and detail.files[1].patch is None


def test_invalid_sha_is_rejected_without_a_request():
    server = FakeServer()
    with pytest.raises(GitHubError):
        client(server).get_commit("../../etc/passwd")
    assert server.requests == []


def test_rate_limit_is_retried():
    server = FakeServer().on(
        "GET", f"/repos/{REPO}", httpx.Response(403, json={"message": "API rate limit exceeded"}, headers={"x-ratelimit-remaining": "0"}), (200, {"full_name": REPO})
    )
    client(server).check_connection()
    assert len(server.requests) == 2


def test_forbidden_without_rate_limit_is_not_retried():
    server = FakeServer().on("GET", f"/repos/{REPO}", (403, {"message": "Resource not accessible by integration"}))
    with pytest.raises(GitHubError, match="not accessible") as info:
        client(server).check_connection()
    assert not info.value.retriable and len(server.requests) == 1


def test_github_unavailable_raises_retriable_error():
    server = FakeServer().on("GET", f"/repos/{REPO}/commits", raise_connect)
    with pytest.raises(GitHubError) as info:
        client(server).list_commits()
    assert info.value.retriable


@pytest.mark.parametrize("reply", [(200, {"not": "a list"}), (200, [{"sha": "1" * 40}]), (200, [{"sha": "short", "commit": {}}])])
def test_malformed_commit_lists_are_rejected(reply):
    server = FakeServer().on("GET", f"/repos/{REPO}/commits", reply)
    with pytest.raises(MalformedResponseError):
        client(server).list_commits()


def test_missing_configuration_is_rejected():
    with pytest.raises(GitHubError):
        HttpGitHubClient(GitHubSettings(token=None, repository=REPO))
