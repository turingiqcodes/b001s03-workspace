"""CI/CD (GitHub Actions) tools (4)."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..schemas import JobId, Owner, Page, PerPage, Repo, RunId, WorkflowRunStatus, page_result, truncate_tail
from ..state import AppState, repo_path, seg
from .common import github_tool


def register(mcp: MCPServer, app: AppState) -> None:
    @github_tool(
        mcp,
        "List GitHub Actions workflow runs, optionally for one workflow (ID or file name such as ci.yml) and "
        "filtered by branch, status/conclusion, or triggering event.",
    )
    async def list_workflow_runs(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        workflow_id: Annotated[str | None, Field(description="Workflow ID or file name, e.g. 'ci.yml'.")] = None,
        branch: Annotated[str | None, Field(description="Only runs for this branch.")] = None,
        status: Annotated[WorkflowRunStatus | None, Field(description="Run status or conclusion.")] = None,
        event: Annotated[str | None, Field(description="Triggering event, e.g. 'push' or 'pull_request'.")] = None,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        path = (repo_path(owner, repo, "actions", "workflows", seg(workflow_id), "runs") if workflow_id
                else repo_path(owner, repo, "actions", "runs"))
        params = {"branch": branch, "status": status, "event": event, "page": page, "per_page": per_page}
        async with app.client_for(ctx.headers) as gh:
            data = await gh.get_json(path, params)
        return page_result(data.get("workflow_runs", []), page, per_page, total_count=data.get("total_count"))

    @github_tool(mcp, "Get one workflow run: status, conclusion, branch, commit, timing, and URLs.")
    async def get_workflow_run(owner: Owner, repo: Repo, run_id: RunId, ctx: Context) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            return await gh.get_json(repo_path(owner, repo, "actions", "runs", run_id))

    @github_tool(mcp, "List the jobs (and their steps) of a workflow run. filter='latest' (default) or 'all' attempts.")
    async def list_workflow_run_jobs(
        owner: Owner,
        repo: Repo,
        run_id: RunId,
        ctx: Context,
        filter: Literal["latest", "all"] = "latest",
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            data = await gh.get_json(repo_path(owner, repo, "actions", "runs", run_id, "jobs"),
                                     {"filter": filter, "page": page, "per_page": per_page})
        return page_result(data.get("jobs", []), page, per_page, total_count=data.get("total_count"))

    @github_tool(
        mcp,
        "Download the plain-text log of a workflow job. Long logs are truncated from the start so the END of the "
        "log (where failures usually appear) is kept.",
    )
    async def get_workflow_job_logs(
        owner: Owner,
        repo: Repo,
        job_id: JobId,
        ctx: Context,
        max_chars: Annotated[
            int | None, Field(ge=100, description="Characters to keep (defaults to the server limit).")
        ] = None,
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            log = await gh.get_text(repo_path(owner, repo, "actions", "jobs", job_id, "logs"), accept="*/*")
        limit = min(max_chars or app.settings.max_text_chars, app.settings.max_text_chars)
        text, truncated = truncate_tail(log, limit)
        return {
            "job_id": job_id,
            "log": text,
            "truncated": truncated,
            "total_chars": len(log),
            "returned_chars": len(text),
            "note": f"Showing the last {len(text)} of {len(log)} characters." if truncated else None,
        }
