"""Pull request and CI/CD tools."""

import httpx

from .conftest import call, connect, error_text

PR = "/repos/o/r/pulls/7"


async def test_get_pull_request(gh):
    gh.add("GET", PR, {"number": 7, "title": "Fix"})
    r = await call(gh, "get_pull_request", {"owner": "o", "repo": "r", "pull_number": 7})
    assert r.structured_content["title"] == "Fix"


async def test_pull_number_must_be_positive(gh):
    r = await call(gh, "get_pull_request", {"owner": "o", "repo": "r", "pull_number": 0})
    assert r.is_error and gh.requests == []


async def test_diff_requested_with_diff_media_type(gh):
    gh.add("GET", PR, httpx.Response(200, text="diff --git a/x b/x\n+1\n"))
    r = await call(gh, "get_pull_request_diff", {"owner": "o", "repo": "r", "pull_number": 7})
    assert gh.requests[0].headers["Accept"] == "application/vnd.github.diff"
    assert r.structured_content["diff"].startswith("diff --git") and r.structured_content["truncated"] is False


async def test_diff_truncation_keeps_head(gh):
    diff = "diff --git a/x b/x\n" + "+line\n" * 1000
    gh.add("GET", PR, httpx.Response(200, text=diff))
    r = await call(gh, "get_pull_request_diff", {"owner": "o", "repo": "r", "pull_number": 7}, max_text_chars=200)
    sc = r.structured_content
    assert sc["truncated"] is True and sc["returned_chars"] == 200 and sc["total_chars"] == len(diff)
    assert sc["diff"] == diff[:200] and "list_pull_request_files" in sc["note"]


async def test_files_reviews_and_comments(gh):
    gh.add("GET", PR + "/files", [{"filename": "a.py"}])
    gh.add("GET", PR + "/reviews", [{"id": 1, "state": "APPROVED"}])
    gh.add("GET", "/repos/o/r/issues/7/comments", [{"id": 10}])
    gh.add("GET", PR + "/comments", [{"id": 20}])
    args = {"owner": "o", "repo": "r", "pull_number": 7}
    async with connect(gh) as c:
        assert (await c.call_tool("list_pull_request_files", args)).structured_content["items"][0]["filename"] == "a.py"
        assert (await c.call_tool("list_pull_request_reviews", args)).structured_content["count"] == 1
        both = (await c.call_tool("list_pull_request_comments", args)).structured_content
        assert both["issue_comments"] == [{"id": 10}] and both["review_comments"] == [{"id": 20}]
        only = (await c.call_tool("list_pull_request_comments", {**args, "kind": "review"})).structured_content
        assert "issue_comments" not in only


async def test_conversation_comment(gh):
    gh.add("POST", "/repos/o/r/issues/7/comments", httpx.Response(201, json={"id": 99}))
    r = await call(gh, "create_pull_request_comment", {"owner": "o", "repo": "r", "pull_number": 7, "body": "LGTM"})
    assert r.structured_content["status"] == "completed" and r.structured_content["result"]["id"] == 99
    assert gh.body() == {"body": "LGTM"}


async def test_inline_comment_resolves_head_sha(gh):
    gh.add("GET", PR, {"head": {"sha": "headsha"}})
    gh.add("POST", PR + "/comments", httpx.Response(201, json={"id": 5}))
    r = await call(gh, "create_pull_request_comment",
                   {"owner": "o", "repo": "r", "pull_number": 7, "body": "nit", "path": "a.py", "line": 3})
    assert not r.is_error
    assert gh.body() == {"body": "nit", "path": "a.py", "line": 3, "side": "RIGHT", "commit_id": "headsha"}


async def test_inline_comment_needs_path_and_line(gh):
    r = await call(gh, "create_pull_request_comment",
                   {"owner": "o", "repo": "r", "pull_number": 7, "body": "x", "path": "a.py"})
    assert "both `path` and `line`" in error_text(r)


async def test_two_step_pending_review(gh):
    gh.add("POST", PR + "/reviews", httpx.Response(200, json={"id": 555, "state": "PENDING"}))
    gh.add("POST", PR + "/reviews/555/events", {"id": 555, "state": "APPROVED"})
    async with connect(gh) as c:
        created = (await c.call_tool("create_pull_request_review", {
            "owner": "o", "repo": "r", "pull_number": 7, "body": "Looks good",
            "comments": [{"path": "a.py", "line": 2, "body": "nice"}]})).structured_content
        assert created["review_id"] == 555 and created["review_state"] == "PENDING"
        assert "submit_pull_request_review" in created["next_step"]
        assert "event" not in gh.body(-1)
        assert gh.body(-1)["comments"] == [{"path": "a.py", "line": 2, "body": "nice"}]

        submitted = (await c.call_tool("submit_pull_request_review", {
            "owner": "o", "repo": "r", "pull_number": 7, "review_id": created["review_id"],
            "event": "APPROVE"})).structured_content
        assert submitted["review_state"] == "APPROVED"
        assert gh.body(-1) == {"event": "APPROVE"}


async def test_review_with_immediate_verdict_and_validation(gh):
    gh.add("POST", PR + "/reviews", {"id": 1, "state": "CHANGES_REQUESTED"})
    r = await call(gh, "create_pull_request_review",
                   {"owner": "o", "repo": "r", "pull_number": 7, "event": "REQUEST_CHANGES", "body": "fix x"})
    assert "next_step" not in r.structured_content and gh.body()["event"] == "REQUEST_CHANGES"
    r = await call(gh, "create_pull_request_review",
                   {"owner": "o", "repo": "r", "pull_number": 7, "event": "REQUEST_CHANGES"})
    assert "body is required" in error_text(r)
    r = await call(gh, "submit_pull_request_review",
                   {"owner": "o", "repo": "r", "pull_number": 7, "review_id": 1, "event": "DISMISS"})
    assert r.is_error


async def test_create_draft_pr_from_fork(gh):
    gh.add("POST", "/repos/o/r/pulls", httpx.Response(201, json={"number": 8, "html_url": "u"}))
    r = await call(gh, "create_pull_request", {"owner": "o", "repo": "r", "title": "Feat", "head": "forker:feature/x",
                                               "base": "main", "draft": True, "body": "desc"})
    assert r.structured_content["number"] == 8
    assert gh.body() == {"title": "Feat", "head": "forker:feature/x", "base": "main", "draft": True, "body": "desc"}


async def test_create_pr_rejects_bad_head_and_empty_title(gh):
    r = await call(gh, "create_pull_request", {"owner": "o", "repo": "r", "title": "T", "head": "a:b:c", "base": "main"})
    assert "owner:branch" in error_text(r)
    r = await call(gh, "create_pull_request", {"owner": "o", "repo": "r", "title": "  ", "head": "x", "base": "main"})
    assert r.is_error and gh.requests == []


async def test_update_pr(gh):
    gh.add("PATCH", PR, {"number": 7, "state": "closed"})
    r = await call(gh, "update_pull_request", {"owner": "o", "repo": "r", "pull_number": 7, "state": "closed",
                                               "title": "New"})
    assert gh.body() == {"title": "New", "state": "closed"} and r.structured_content["status"] == "completed"
    r = await call(gh, "update_pull_request", {"owner": "o", "repo": "r", "pull_number": 7})
    assert "at least one field" in error_text(r)


async def test_merge_methods(gh):
    gh.add("PUT", PR + "/merge", {"merged": True, "sha": "m"})
    for method in ("merge", "squash", "rebase"):
        r = await call(gh, "merge_pull_request", {"owner": "o", "repo": "r", "pull_number": 7, "merge_method": method})
        assert r.structured_content["merged"] is True and gh.body()["merge_method"] == method
    count = len(gh.requests)
    r = await call(gh, "merge_pull_request", {"owner": "o", "repo": "r", "pull_number": 7, "merge_method": "fast-forward"})
    assert r.is_error and len(gh.requests) == count


async def test_merge_conflict_error_mapped(gh):
    gh.add("PUT", PR + "/merge", httpx.Response(405, json={"message": "Pull Request is not mergeable"}))
    r = await call(gh, "merge_pull_request", {"owner": "o", "repo": "r", "pull_number": 7})
    assert "405" in error_text(r) and "not mergeable" in error_text(r)


# ------------------------------------------------------------------ CI/CD
async def test_list_workflow_runs_filters(gh):
    gh.add("GET", "/repos/o/r/actions/workflows/ci.yml/runs", {"total_count": 1, "workflow_runs": [{"id": 1}]})
    r = await call(gh, "list_workflow_runs", {"owner": "o", "repo": "r", "workflow_id": "ci.yml",
                                              "branch": "main", "status": "failure"})
    assert r.structured_content["items"] == [{"id": 1}] and r.structured_content["total_count"] == 1
    params = gh.requests[0].url.params
    assert params["branch"] == "main" and params["status"] == "failure" and "event" not in params


async def test_list_workflow_runs_repo_wide_and_invalid_status(gh):
    gh.add("GET", "/repos/o/r/actions/runs", {"total_count": 0, "workflow_runs": []})
    assert not (await call(gh, "list_workflow_runs", {"owner": "o", "repo": "r"})).is_error
    assert (await call(gh, "list_workflow_runs", {"owner": "o", "repo": "r", "status": "exploded"})).is_error


async def test_get_run_and_jobs(gh):
    gh.add("GET", "/repos/o/r/actions/runs/42", {"id": 42, "conclusion": "failure"})
    gh.add("GET", "/repos/o/r/actions/runs/42/jobs", {"total_count": 1, "jobs": [{"id": 9}]})
    async with connect(gh) as c:
        assert (await c.call_tool("get_workflow_run", {"owner": "o", "repo": "r", "run_id": 42})
                ).structured_content["conclusion"] == "failure"
        jobs = (await c.call_tool("list_workflow_run_jobs", {"owner": "o", "repo": "r", "run_id": 42, "filter": "all"})
                ).structured_content
    assert jobs["items"] == [{"id": 9}] and gh.requests[-1].url.params["filter"] == "all"


async def test_job_logs_follow_redirect_and_keep_tail(gh):
    log = "setup\n" * 500 + "ERROR: test_x failed\n"
    gh.add("GET", "/repos/o/r/actions/jobs/9/logs",
           httpx.Response(302, headers={"Location": "https://blob.example.com/log.txt"}))
    gh.add("GET", "/log.txt", httpx.Response(200, text=log))
    r = await call(gh, "get_workflow_job_logs", {"owner": "o", "repo": "r", "job_id": 9}, max_text_chars=300)
    sc = r.structured_content
    assert sc["truncated"] is True and sc["log"].endswith("ERROR: test_x failed\n")
    assert len(sc["log"]) == 300 and sc["total_chars"] == len(log)
    # The auth header must not be forwarded to the third-party log host.
    assert "Authorization" not in gh.requests[1].headers


async def test_job_logs_short_not_truncated_and_max_chars(gh):
    gh.add("GET", "/repos/o/r/actions/jobs/9/logs", httpx.Response(200, text="x" * 1000))
    r = await call(gh, "get_workflow_job_logs", {"owner": "o", "repo": "r", "job_id": 9})
    assert r.structured_content["truncated"] is False
    r = await call(gh, "get_workflow_job_logs", {"owner": "o", "repo": "r", "job_id": 9, "max_chars": 100})
    assert r.structured_content["returned_chars"] == 100
