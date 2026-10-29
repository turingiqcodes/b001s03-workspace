"""Reusable Pydantic-validated input types and result helpers for tool signatures.

Tools declare flat, annotated parameters; the MCP SDK builds a Pydantic model
from the signature, so these constraints are enforced on every call *and*
published in each tool's JSON input schema.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

Owner = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$"),
    Field(description="Repository owner (user or organization login)."),
]
Repo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$"),
    Field(description="Repository name."),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PullNumber = Annotated[int, Field(ge=1, description="Pull request number.")]
IssueNumber = Annotated[int, Field(ge=1, description="Issue number.")]
RunId = Annotated[int, Field(ge=1, description="Workflow run ID.")]
JobId = Annotated[int, Field(ge=1, description="Workflow job ID.")]
Ref = Annotated[
    str | None,
    Field(description="Branch, tag, or commit SHA. Defaults to the repository's default branch."),
]
Page = Annotated[int, Field(ge=1, description="Page number (1-based).")]
PerPage = Annotated[int, Field(ge=1, le=100, description="Results per page (1-100).")]
DryRun = Annotated[
    bool,
    Field(
        description="Preview the request without changing anything on GitHub. "
        "Always on when the server runs with GITHUB_DRY_RUN=true."
    ),
]
Labels = Annotated[
    list[NonEmptyStr],
    Field(min_length=1, description="One or more label names."),
]

IssueState = Literal["open", "closed", "all"]
ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
MergeMethod = Literal["merge", "squash", "rebase"]
CloseReason = Literal["completed", "not_planned"]
WorkflowRunStatus = Literal[
    "completed", "action_required", "cancelled", "failure", "neutral", "skipped", "stale",
    "success", "timed_out", "in_progress", "queued", "requested", "waiting", "pending",
]


class ReviewComment(BaseModel):
    """An inline comment attached to a pull request review."""

    path: NonEmptyStr = Field(description="File path relative to the repository root.")
    body: NonEmptyStr = Field(description="Comment text (Markdown).")
    line: int | None = Field(default=None, ge=1, description="Line in the diff to comment on.")
    side: Literal["LEFT", "RIGHT"] | None = Field(default=None, description="Diff side for `line`.")
    start_line: int | None = Field(default=None, ge=1, description="First line of a multi-line comment.")
    start_side: Literal["LEFT", "RIGHT"] | None = None


def page_result(items: list[Any], page: int, per_page: int, **extra: Any) -> dict[str, Any]:
    """Wrap a list in an object so it can be returned as MCP structured content."""
    return {
        "items": items,
        "count": len(items),
        "page": page,
        "per_page": per_page,
        "has_more_hint": len(items) >= per_page,
        **extra,
    }


def truncate_head(text: str, limit: int) -> tuple[str, bool]:
    """Keep the beginning of ``text``."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def truncate_tail(text: str, limit: int) -> tuple[str, bool]:
    """Keep the end of ``text`` (where errors usually are in CI logs)."""
    if len(text) <= limit:
        return text, False
    return text[-limit:], True
