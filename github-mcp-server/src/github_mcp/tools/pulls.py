"""Pull request tools (11)."""

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..schemas import (
    DryRun, MergeMethod, NonEmptyStr, Owner, Page, PerPage, PullNumber, Repo, ReviewComment, ReviewEvent,
    page_result, truncate_head,
)
from ..state import AppState, completed, dry_run_preview, repo_path
from .common import github_tool

# "branch" or "fork-owner:branch" for cross-repository pull requests.
HEAD_PATTERN = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?:)?[^\s:~^?*\[\\]+$")


def register(mcp: MCPServer, app: AppState) -> None:
    @github_tool(mcp, "Get pull request metadata: title, body, state, draft flag, head/base refs, mergeability.")
    async def get_pull_request(owner: Owner, repo: Repo, pull_number: PullNumber, ctx: Context) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            return await gh.get_json(repo_path(owner, repo, "pulls", pull_number))

    @github_tool(
        mcp,
        "Get the unified diff of a pull request. Long diffs are truncated (keeping the beginning) and flagged.",
    )
    async def get_pull_request_diff(
        owner: Owner, repo: Repo, pull_number: PullNumber, ctx: Context
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            diff = await gh.get_text(repo_path(owner, repo, "pulls", pull_number), accept="application/vnd.github.diff")
        text, truncated = truncate_head(diff, app.settings.max_text_chars)
        return {
            "pull_number": pull_number,
            "diff": text,
            "truncated": truncated,
            "total_chars": len(diff),
            "returned_chars": len(text),
            "note": "Diff truncated; use list_pull_request_files for per-file patches." if truncated else None,
        }

    @github_tool(mcp, "List files changed in a pull request with status, additions, deletions, and patch.")
    async def list_pull_request_files(
        owner: Owner, repo: Repo, pull_number: PullNumber, ctx: Context, page: Page = 1, per_page: PerPage = 30
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            items = await gh.get_json(repo_path(owner, repo, "pulls", pull_number, "files"),
                                      {"page": page, "per_page": per_page})
        return page_result(items, page, per_page)

    @github_tool(
        mcp,
        "List pull request comments. kind='issue' returns conversation comments, kind='review' returns inline "
        "code review comments, kind='all' (default) returns both, paginated independently.",
    )
    async def list_pull_request_comments(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        ctx: Context,
        kind: Literal["all", "issue", "review"] = "all",
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        params = {"page": page, "per_page": per_page}
        result: dict[str, Any] = {"page": page, "per_page": per_page}
        async with app.client_for(ctx.headers) as gh:
            if kind in ("all", "issue"):
                result["issue_comments"] = await gh.get_json(
                    repo_path(owner, repo, "issues", pull_number, "comments"), params)
            if kind in ("all", "review"):
                result["review_comments"] = await gh.get_json(
                    repo_path(owner, repo, "pulls", pull_number, "comments"), params)
        return result

    @github_tool(mcp, "List reviews on a pull request (approvals, change requests, comments, pending reviews).")
    async def list_pull_request_reviews(
        owner: Owner, repo: Repo, pull_number: PullNumber, ctx: Context, page: Page = 1, per_page: PerPage = 30
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            items = await gh.get_json(repo_path(owner, repo, "pulls", pull_number, "reviews"),
                                      {"page": page, "per_page": per_page})
        return page_result(items, page, per_page)

    @github_tool(
        mcp,
        "Comment on a pull request. Without `path` this posts a conversation comment. With `path` and `line` it "
        "posts an inline review comment on the diff (commit_id defaults to the PR head SHA).",
        read_only=False,
        idempotent=False,
    )
    async def create_pull_request_comment(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        body: Annotated[NonEmptyStr, Field(description="Comment text (Markdown).")],
        ctx: Context,
        path: Annotated[str | None, Field(description="File path for an inline comment.")] = None,
        line: Annotated[int | None, Field(ge=1, description="Diff line for an inline comment.")] = None,
        side: Literal["LEFT", "RIGHT"] = "RIGHT",
        commit_id: Annotated[str | None, Field(description="Commit SHA for inline comments.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        if (path is None) != (line is None):
            raise ValueError("Inline comments need both `path` and `line`; omit both for a conversation comment.")
        dry = app.is_dry_run(dry_run)
        if path is None:
            api_path = repo_path(owner, repo, "issues", pull_number, "comments")
            payload: dict[str, Any] = {"body": body}
            if dry:
                return dry_run_preview("create_pull_request_comment", "POST", api_path, payload)
            async with app.client_for(ctx.headers) as gh:
                return completed("create_pull_request_comment", await gh.write_json("POST", api_path, payload))

        api_path = repo_path(owner, repo, "pulls", pull_number, "comments")
        payload = {"body": body, "path": path, "line": line, "side": side, "commit_id": commit_id}
        if dry:
            if commit_id is None:
                payload["commit_id"] = "<pull request head SHA, resolved when executed>"
            return dry_run_preview("create_pull_request_comment", "POST", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            if commit_id is None:
                pr = await gh.get_json(repo_path(owner, repo, "pulls", pull_number))
                payload["commit_id"] = pr["head"]["sha"]
            return completed("create_pull_request_comment", await gh.write_json("POST", api_path, payload))

    @github_tool(
        mcp,
        "Create a pull request review. Omit `event` to create a PENDING review (returns review_id; finish it with "
        "submit_pull_request_review). Pass event=APPROVE, REQUEST_CHANGES, or COMMENT to submit immediately.",
        read_only=False,
        idempotent=False,
    )
    async def create_pull_request_review(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        ctx: Context,
        body: Annotated[str | None, Field(description="Review summary (Markdown).")] = None,
        event: Annotated[ReviewEvent | None, Field(description="Verdict; omit for a pending review.")] = None,
        comments: Annotated[list[ReviewComment] | None, Field(description="Inline review comments.")] = None,
        commit_id: Annotated[str | None, Field(description="Commit SHA to review; defaults to PR head.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        if event == "REQUEST_CHANGES" and not (body and body.strip()):
            raise ValueError("A body is required when event is REQUEST_CHANGES.")
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if event is not None:
            payload["event"] = event
        if comments:
            payload["comments"] = [c.model_dump(exclude_none=True) for c in comments]
        if commit_id:
            payload["commit_id"] = commit_id
        api_path = repo_path(owner, repo, "pulls", pull_number, "reviews")
        if app.is_dry_run(dry_run):
            return dry_run_preview("create_pull_request_review", "POST", api_path, payload,
                                   pending=event is None)
        async with app.client_for(ctx.headers) as gh:
            review = await gh.write_json("POST", api_path, payload)
        extra: dict[str, Any] = {"review_id": review.get("id"), "review_state": review.get("state")}
        if event is None:
            extra["next_step"] = (
                f"Review {review.get('id')} is PENDING. Call submit_pull_request_review with this review_id "
                "and event APPROVE, REQUEST_CHANGES, or COMMENT to publish it."
            )
        return completed("create_pull_request_review", review, **extra)

    @github_tool(
        mcp,
        "Submit a PENDING pull request review (created by create_pull_request_review without an event) with "
        "event APPROVE, REQUEST_CHANGES, or COMMENT.",
        read_only=False,
        idempotent=False,
    )
    async def submit_pull_request_review(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        review_id: Annotated[int, Field(ge=1, description="ID of the pending review.")],
        event: ReviewEvent,
        ctx: Context,
        body: Annotated[str | None, Field(description="Optional review summary.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        if event == "REQUEST_CHANGES" and not (body and body.strip()):
            raise ValueError("A body is required when event is REQUEST_CHANGES.")
        payload: dict[str, Any] = {"event": event}
        if body is not None:
            payload["body"] = body
        api_path = repo_path(owner, repo, "pulls", pull_number, "reviews", review_id, "events")
        if app.is_dry_run(dry_run):
            return dry_run_preview("submit_pull_request_review", "POST", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            review = await gh.write_json("POST", api_path, payload)
        return completed("submit_pull_request_review", review, review_id=review.get("id"),
                         review_state=review.get("state"))

    @github_tool(
        mcp,
        "Open a pull request (optionally as a draft). For a branch on a fork, set head to 'fork-owner:branch'.",
        read_only=False,
        idempotent=False,
    )
    async def create_pull_request(
        owner: Owner,
        repo: Repo,
        title: Annotated[NonEmptyStr, Field(description="Pull request title.")],
        head: Annotated[NonEmptyStr, Field(description="Source branch, or 'owner:branch' for a fork.")],
        base: Annotated[NonEmptyStr, Field(description="Target branch to merge into.")],
        ctx: Context,
        body: Annotated[str | None, Field(description="Description (Markdown).")] = None,
        draft: bool = False,
        maintainer_can_modify: bool | None = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        if not HEAD_PATTERN.match(head):
            raise ValueError("head must be a branch name or 'owner:branch' (for a fork).")
        payload: dict[str, Any] = {"title": title, "head": head, "base": base, "draft": draft}
        if body is not None:
            payload["body"] = body
        if maintainer_can_modify is not None:
            payload["maintainer_can_modify"] = maintainer_can_modify
        api_path = repo_path(owner, repo, "pulls")
        if app.is_dry_run(dry_run):
            return dry_run_preview("create_pull_request", "POST", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            pr = await gh.write_json("POST", api_path, payload)
        return completed("create_pull_request", pr, number=pr.get("number"), html_url=pr.get("html_url"))

    @github_tool(
        mcp,
        "Update a pull request's title, body, base branch, state (open/closed), or maintainer_can_modify. "
        "Only the fields you pass are changed.",
        read_only=False,
        idempotent=True,
    )
    async def update_pull_request(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        ctx: Context,
        title: Annotated[NonEmptyStr | None, Field(description="New title.")] = None,
        body: Annotated[str | None, Field(description="New description.")] = None,
        state: Annotated[Literal["open", "closed"] | None, Field(description="open or closed.")] = None,
        base: Annotated[NonEmptyStr | None, Field(description="New base branch.")] = None,
        maintainer_can_modify: bool | None = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        payload = {k: v for k, v in {"title": title, "body": body, "state": state, "base": base,
                                      "maintainer_can_modify": maintainer_can_modify}.items() if v is not None}
        if not payload:
            raise ValueError("Provide at least one field to update (title, body, state, base, maintainer_can_modify).")
        api_path = repo_path(owner, repo, "pulls", pull_number)
        if app.is_dry_run(dry_run):
            return dry_run_preview("update_pull_request", "PATCH", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            return completed("update_pull_request", await gh.write_json("PATCH", api_path, payload))

    @github_tool(
        mcp,
        "Merge a pull request using merge_method 'merge', 'squash', or 'rebase'. Pass `sha` to merge only if "
        "the head still matches.",
        read_only=False,
        destructive=True,
        idempotent=False,
    )
    async def merge_pull_request(
        owner: Owner,
        repo: Repo,
        pull_number: PullNumber,
        ctx: Context,
        merge_method: MergeMethod = "merge",
        commit_title: str | None = None,
        commit_message: str | None = None,
        sha: Annotated[str | None, Field(description="Expected head SHA; merge fails if it differs.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        payload = {k: v for k, v in {"merge_method": merge_method, "commit_title": commit_title,
                                      "commit_message": commit_message, "sha": sha}.items() if v is not None}
        api_path = repo_path(owner, repo, "pulls", pull_number, "merge")
        if app.is_dry_run(dry_run):
            return dry_run_preview("merge_pull_request", "PUT", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            result = await gh.write_json("PUT", api_path, payload)
        return completed("merge_pull_request", result, merged=result.get("merged"), sha=result.get("sha"))
