"""Tool registration. The inventory below is the single source of truth (28 tools)."""

from mcp.server.mcpserver import MCPServer

from ..state import AppState
from . import actions, issues, pulls, repos, search

REPOSITORY_TOOLS = (
    "get_repository", "list_repository_branches", "list_repository_files", "get_file_contents",
    "get_repository_commits",
)
PULL_REQUEST_TOOLS = (
    "get_pull_request", "get_pull_request_diff", "list_pull_request_files", "list_pull_request_comments",
    "list_pull_request_reviews", "create_pull_request_comment", "create_pull_request_review",
    "submit_pull_request_review", "create_pull_request", "update_pull_request", "merge_pull_request",
)
CICD_TOOLS = ("list_workflow_runs", "get_workflow_run", "list_workflow_run_jobs", "get_workflow_job_logs")
ISSUE_TOOLS = ("get_issue", "list_issues", "create_issue", "add_labels", "update_issue", "close_issue")
SEARCH_TOOLS = ("search_issues", "search_code")

ALL_TOOLS = REPOSITORY_TOOLS + PULL_REQUEST_TOOLS + CICD_TOOLS + ISSUE_TOOLS + SEARCH_TOOLS

WRITE_TOOLS = (
    "create_pull_request_comment", "create_pull_request_review", "submit_pull_request_review",
    "create_pull_request", "update_pull_request", "merge_pull_request",
    "create_issue", "add_labels", "update_issue", "close_issue",
)


def register_all(mcp: MCPServer, app: AppState) -> None:
    for module in (repos, pulls, actions, issues, search):
        module.register(mcp, app)
