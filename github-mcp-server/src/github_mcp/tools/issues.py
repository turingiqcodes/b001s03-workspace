"""Issue tools (6)."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..schemas import (
    CloseReason, DryRun, IssueNumber, IssueState, Labels, NonEmptyStr, Owner, Page, PerPage, Repo, page_result,
)
from ..state import AppState, completed, dry_run_preview, repo_path
from .common import github_tool


def register(mcp: MCPServer, app: AppState) -> None:
    @github_tool(mcp, "Get an issue by number (title, body, state, labels, assignees, milestone).")
    async def get_issue(owner: Owner, repo: Repo, issue_number: IssueNumber, ctx: Context) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            return await gh.get_json(repo_path(owner, repo, "issues", issue_number))

    @github_tool(
        mcp,
        "List repository issues filtered by state and labels. Pull requests (which GitHub's issues API also "
        "returns) are hidden unless include_pull_requests=true; hidden items are counted in `filtered_pull_requests`.",
    )
    async def list_issues(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        state: IssueState = "open",
        labels: Annotated[list[NonEmptyStr] | None, Field(description="Issues must have ALL these labels.")] = None,
        assignee: Annotated[str | None, Field(description="Login, 'none', or '*'.")] = None,
        since: Annotated[str | None, Field(description="Only issues updated at or after this ISO 8601 time.")] = None,
        sort: Literal["created", "updated", "comments"] = "created",
        direction: Literal["asc", "desc"] = "desc",
        include_pull_requests: bool = False,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        params = {"state": state, "labels": ",".join(labels) if labels else None, "assignee": assignee,
                  "since": since, "sort": sort, "direction": direction, "page": page, "per_page": per_page}
        async with app.client_for(ctx.headers) as gh:
            items = await gh.get_json(repo_path(owner, repo, "issues"), params)
        raw_count = len(items)
        if not include_pull_requests:
            items = [i for i in items if "pull_request" not in i]
        result = page_result(items, page, per_page, filtered_pull_requests=raw_count - len(items))
        result["has_more_hint"] = raw_count >= per_page
        return result

    @github_tool(mcp, "Create an issue. Title must be non-empty.", read_only=False, idempotent=False)
    async def create_issue(
        owner: Owner,
        repo: Repo,
        title: Annotated[NonEmptyStr, Field(description="Issue title (non-empty).")],
        ctx: Context,
        body: Annotated[str | None, Field(description="Issue body (Markdown).")] = None,
        labels: Annotated[list[NonEmptyStr] | None, Field(description="Labels to apply.")] = None,
        assignees: Annotated[list[NonEmptyStr] | None, Field(description="Logins to assign.")] = None,
        milestone: Annotated[int | None, Field(ge=1, description="Milestone number.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        payload = {k: v for k, v in {"title": title, "body": body, "labels": labels, "assignees": assignees,
                                      "milestone": milestone}.items() if v is not None}
        api_path = repo_path(owner, repo, "issues")
        if app.is_dry_run(dry_run):
            return dry_run_preview("create_issue", "POST", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            issue = await gh.write_json("POST", api_path, payload)
        return completed("create_issue", issue, number=issue.get("number"), html_url=issue.get("html_url"))

    @github_tool(
        mcp,
        "Add labels to an issue or pull request, keeping existing labels (append semantics). "
        "The list must contain at least one non-empty label.",
        read_only=False,
        idempotent=True,
    )
    async def add_labels(
        owner: Owner, repo: Repo, issue_number: IssueNumber, labels: Labels, ctx: Context, dry_run: DryRun = False
    ) -> dict[str, Any]:
        payload = {"labels": labels}
        api_path = repo_path(owner, repo, "issues", issue_number, "labels")
        if app.is_dry_run(dry_run):
            return dry_run_preview("add_labels", "POST", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            current = await gh.write_json("POST", api_path, payload)
        return completed("add_labels", current, labels=[label.get("name") for label in current])

    @github_tool(
        mcp,
        "Update an issue. Only fields you pass change. IMPORTANT: `labels` and `assignees` REPLACE the full "
        "existing set (pass [] to clear all); omit them to leave them unchanged. Use add_labels to append labels.",
        read_only=False,
        idempotent=True,
    )
    async def update_issue(
        owner: Owner,
        repo: Repo,
        issue_number: IssueNumber,
        ctx: Context,
        title: Annotated[NonEmptyStr | None, Field(description="New title (non-empty).")] = None,
        body: Annotated[str | None, Field(description="New body.")] = None,
        state: Annotated[Literal["open", "closed"] | None, Field(description="open or closed.")] = None,
        state_reason: Annotated[
            Literal["completed", "not_planned", "reopened"] | None, Field(description="Reason for a state change.")
        ] = None,
        labels: Annotated[
            list[NonEmptyStr] | None, Field(description="REPLACES all labels; [] removes every label.")
        ] = None,
        assignees: Annotated[
            list[NonEmptyStr] | None, Field(description="REPLACES all assignees; [] removes every assignee.")
        ] = None,
        milestone: Annotated[int | None, Field(ge=1, description="Milestone number.")] = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        payload = {k: v for k, v in {"title": title, "body": body, "state": state, "state_reason": state_reason,
                                      "labels": labels, "assignees": assignees,
                                      "milestone": milestone}.items() if v is not None}
        if not payload:
            raise ValueError("Provide at least one field to update.")
        replaced = [f for f in ("labels", "assignees") if f in payload]
        notes = {"replaced_fields": replaced}
        if replaced:
            notes["replacement_note"] = (
                "The existing " + " and ".join(replaced) + " are replaced by exactly the values sent."
            )
        api_path = repo_path(owner, repo, "issues", issue_number)
        if app.is_dry_run(dry_run):
            return dry_run_preview("update_issue", "PATCH", api_path, payload, **notes)
        async with app.client_for(ctx.headers) as gh:
            return completed("update_issue", await gh.write_json("PATCH", api_path, payload), **notes)

    @github_tool(
        mcp,
        "Close an issue with reason 'completed' (default) or 'not_planned'.",
        read_only=False,
        idempotent=True,
    )
    async def close_issue(
        owner: Owner,
        repo: Repo,
        issue_number: IssueNumber,
        ctx: Context,
        reason: CloseReason = "completed",
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        payload = {"state": "closed", "state_reason": reason}
        api_path = repo_path(owner, repo, "issues", issue_number)
        if app.is_dry_run(dry_run):
            return dry_run_preview("close_issue", "PATCH", api_path, payload)
        async with app.client_for(ctx.headers) as gh:
            issue = await gh.write_json("PATCH", api_path, payload)
        return completed("close_issue", issue, state=issue.get("state"), state_reason=issue.get("state_reason"))
