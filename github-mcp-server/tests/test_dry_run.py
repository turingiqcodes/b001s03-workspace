"""Dry-run mode: every write tool returns a preview and sends zero requests to GitHub."""

import pytest

from github_mcp.tools import WRITE_TOOLS

from .conftest import call

BASE = {"owner": "o", "repo": "r"}
WRITE_CALLS = {
    "create_pull_request_comment": ({**BASE, "pull_number": 1, "body": "hi"}, "POST", "/repos/o/r/issues/1/comments"),
    "create_pull_request_review": ({**BASE, "pull_number": 1, "body": "b"}, "POST", "/repos/o/r/pulls/1/reviews"),
    "submit_pull_request_review": ({**BASE, "pull_number": 1, "review_id": 5, "event": "APPROVE"}, "POST",
                                   "/repos/o/r/pulls/1/reviews/5/events"),
    "create_pull_request": ({**BASE, "title": "t", "head": "f:b", "base": "main", "draft": True}, "POST",
                            "/repos/o/r/pulls"),
    "update_pull_request": ({**BASE, "pull_number": 1, "state": "closed"}, "PATCH", "/repos/o/r/pulls/1"),
    "merge_pull_request": ({**BASE, "pull_number": 1, "merge_method": "squash"}, "PUT", "/repos/o/r/pulls/1/merge"),
    "create_issue": ({**BASE, "title": "t"}, "POST", "/repos/o/r/issues"),
    "add_labels": ({**BASE, "issue_number": 1, "labels": ["bug"]}, "POST", "/repos/o/r/issues/1/labels"),
    "update_issue": ({**BASE, "issue_number": 1, "labels": []}, "PATCH", "/repos/o/r/issues/1"),
    "close_issue": ({**BASE, "issue_number": 1, "reason": "not_planned"}, "PATCH", "/repos/o/r/issues/1"),
}


def test_every_write_tool_is_covered():
    assert set(WRITE_CALLS) == set(WRITE_TOOLS) and len(WRITE_CALLS) == 10


def assert_preview(result, method, path):
    assert not result.is_error, result.content
    sc = result.structured_content
    assert sc["status"] == "dry_run" and sc["dry_run"] is True
    assert sc["request"]["method"] == method and sc["request"]["path"] == path
    assert "no request was sent" in sc["message"]
    return sc


@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
async def test_global_dry_run_makes_zero_requests(gh, tool):
    args, method, path = WRITE_CALLS[tool]
    result = await call(gh, tool, args, dry_run=True)
    assert_preview(result, method, path)
    assert gh.requests == []


@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
async def test_per_call_dry_run_makes_zero_requests(gh, tool):
    args, method, path = WRITE_CALLS[tool]
    assert_preview(await call(gh, tool, {**args, "dry_run": True}), method, path)
    assert gh.requests == []


@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
async def test_per_call_false_cannot_override_global_dry_run(gh, tool):
    args, method, path = WRITE_CALLS[tool]
    assert_preview(await call(gh, tool, {**args, "dry_run": False}, dry_run=True), method, path)
    assert gh.requests == []


async def test_inline_comment_dry_run_does_not_fetch_head_sha(gh):
    args = {**BASE, "pull_number": 1, "body": "x", "path": "a.py", "line": 2}
    sc = assert_preview(await call(gh, "create_pull_request_comment", args, dry_run=True),
                        "POST", "/repos/o/r/pulls/1/comments")
    assert "resolved when executed" in sc["request"]["body"]["commit_id"] and gh.requests == []


async def test_preview_contains_payload_and_pending_flag(gh):
    sc = (await call(gh, "create_pull_request_review", WRITE_CALLS["create_pull_request_review"][0],
                     dry_run=True)).structured_content
    assert sc["pending"] is True and sc["request"]["body"] == {"body": "b"}
    sc = (await call(gh, "update_issue", WRITE_CALLS["update_issue"][0], dry_run=True)).structured_content
    assert sc["replaced_fields"] == ["labels"]


async def test_completed_writes_are_distinguishable_from_preview(gh):
    gh.add("POST", "/repos/o/r/issues", {"number": 1})
    sc = (await call(gh, "create_issue", {**BASE, "title": "t"})).structured_content
    assert sc["status"] == "completed" and sc["dry_run"] is False


async def test_dry_run_still_validates_input(gh):
    assert (await call(gh, "add_labels", {**BASE, "issue_number": 1, "labels": []}, dry_run=True)).is_error
    assert (await call(gh, "merge_pull_request", {**BASE, "pull_number": 1, "merge_method": "ff"}, dry_run=True)).is_error
