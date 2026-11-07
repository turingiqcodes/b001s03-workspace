"""Linear GraphQL API client (FR-068/069/074..077, NFR-010).

Used by the incident manager, the approval service and the ``oncall-linear-mcp``
server. Queries are retried on transient failures; mutations are not (a timed-out
``issueCreate`` is reported, and duplicate detection by fingerprint covers the case
where it actually succeeded).

Approval evidence (decision A2) comes from two reads:

* :meth:`list_label_events` — issue history entries, each with the *actor* who added
  labels; this is how the approver of an ``oncall:approved`` label is identified.
* :meth:`list_comments` — comments with their author, for ``/approve``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import LinearSettings
from ..exceptions import LinearError, MalformedResponseError
from .http import json_body, parse, send

log = logging.getLogger(__name__)

SERVICE = "linear"
API_URL = "https://api.linear.app/graphql"
MAX_PAGES = 20


# --------------------------------------------------------------------------- models


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class LinearUser(_Model):
    id: str
    name: str | None = None
    email: str | None = None


class LinearLabel(_Model):
    id: str
    name: str


class LinearState(_Model):
    id: str
    name: str
    type: str | None = None


class LinearIssue(_Model):
    id: str
    identifier: str
    title: str
    url: str | None = None
    priority: int | None = None
    description: str | None = None
    state: LinearState | None = None
    assignee: LinearUser | None = None
    labels: list[LinearLabel] = Field(default_factory=list)

    @field_validator("labels", mode="before")
    @classmethod
    def _unwrap_nodes(cls, v: Any) -> Any:
        return v.get("nodes", []) if isinstance(v, dict) else v

    @property
    def label_names(self) -> set[str]:
        return {label.name for label in self.labels}


class LinearComment(_Model):
    id: str
    body: str
    created_at: datetime = Field(alias="createdAt")
    user: LinearUser | None = None


class LabelEvent(_Model):
    """One issue-history entry that changed labels."""

    created_at: datetime = Field(alias="createdAt")
    actor: LinearUser | None = None
    added_label_ids: list[str] = Field(default_factory=list, alias="addedLabelIds")
    removed_label_ids: list[str] = Field(default_factory=list, alias="removedLabelIds")

    @field_validator("added_label_ids", "removed_label_ids", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return v or []


# --------------------------------------------------------------------------- interface


@runtime_checkable
class LinearClient(Protocol):
    def check_connection(self) -> dict[str, Any]: ...
    def viewer(self) -> LinearUser: ...
    def find_label_ids(self, names: list[str]) -> dict[str, str]: ...
    def list_workflow_states(self) -> list[LinearState]: ...
    def find_user(self, identifier: str) -> LinearUser | None: ...
    def create_issue(
        self,
        *,
        title: str,
        description: str,
        priority: int | None = None,
        label_ids: list[str] | None = None,
        assignee_id: str | None = None,
        state_id: str | None = None,
    ) -> LinearIssue: ...
    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        state_id: str | None = None,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> LinearIssue: ...
    def add_comment(self, issue_id: str, body: str) -> LinearComment: ...
    def get_issue(self, issue_id: str) -> LinearIssue: ...
    def search_issues(self, text: str, limit: int = 20) -> list[LinearIssue]: ...
    def list_comments(self, issue_id: str) -> list[LinearComment]: ...
    def list_label_events(self, issue_id: str) -> list[LabelEvent]: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------- GraphQL documents

_USER = "id name email"
_ISSUE = f"id identifier title url priority description state {{ id name type }} assignee {{ {_USER} }} labels {{ nodes {{ id name }} }}"
_COMMENT = f"id body createdAt user {{ {_USER} }}"

Q_VIEWER = f"query {{ viewer {{ {_USER} }} }}"
Q_TEAM = "query($id: String!) { team(id: $id) { id name key } }"
Q_LABELS = "query($names: [String!]!) { issueLabels(first: 250, filter: { name: { in: $names } }) { nodes { id name team { id } } } }"
Q_STATES = "query($teamId: ID!) { workflowStates(first: 100, filter: { team: { id: { eq: $teamId } } }) { nodes { id name type } } }"
Q_USER_BY_EMAIL = f"query($email: String!) {{ users(first: 1, filter: {{ email: {{ eq: $email }} }}) {{ nodes {{ {_USER} }} }} }}"
Q_USER_BY_ID = f"query($id: String!) {{ user(id: $id) {{ {_USER} }} }}"
Q_ISSUE = f"query($id: String!) {{ issue(id: $id) {{ {_ISSUE} }} }}"
Q_SEARCH = (
    "query($teamId: ID!, $text: String!, $first: Int!) { issues(first: $first, filter: { team: { id: { eq: $teamId } }, "
    "or: [{ title: { containsIgnoreCase: $text } }, { description: { containsIgnoreCase: $text } }] }) "
    f"{{ nodes {{ {_ISSUE} }} }} }}"
)
Q_COMMENTS = (
    f"query($id: String!, $after: String) {{ issue(id: $id) {{ comments(first: 100, after: $after) "
    f"{{ nodes {{ {_COMMENT} }} pageInfo {{ hasNextPage endCursor }} }} }} }}"
)
Q_HISTORY = (
    f"query($id: String!, $after: String) {{ issue(id: $id) {{ history(first: 100, after: $after) "
    f"{{ nodes {{ createdAt actor {{ {_USER} }} addedLabelIds removedLabelIds }} pageInfo {{ hasNextPage endCursor }} }} }} }}"
)
M_CREATE = f"mutation($input: IssueCreateInput!) {{ issueCreate(input: $input) {{ success issue {{ {_ISSUE} }} }} }}"
M_UPDATE = f"mutation($id: String!, $input: IssueUpdateInput!) {{ issueUpdate(id: $id, input: $input) {{ success issue {{ {_ISSUE} }} }} }}"
M_COMMENT = f"mutation($input: CommentCreateInput!) {{ commentCreate(input: $input) {{ success comment {{ {_COMMENT} }} }} }}"


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            raise MalformedResponseError(SERVICE, f"missing '{'.'.join(path)}'")
        data = data[key]
    return data


def _is_rate_limited(response: httpx.Response) -> bool:
    try:
        errors = response.json().get("errors") or []
    except (ValueError, AttributeError):
        return False
    return any((e.get("extensions") or {}).get("code") == "RATELIMITED" for e in errors if isinstance(e, dict))


class HttpLinearClient:
    def __init__(self, settings: LinearSettings, *, transport: httpx.BaseTransport | None = None, url: str = API_URL) -> None:
        if settings.api_key is None or not settings.team_id:
            raise LinearError("LINEAR_API_KEY and LINEAR_TEAM_ID must be configured")
        self._settings = settings
        self._team_id = settings.team_id
        self._url = url
        kwargs: dict[str, Any] = {
            "timeout": settings.timeout_seconds,
            # Linear personal API keys are sent as-is (no "Bearer" prefix).
            "headers": {"Authorization": settings.api_key.get_secret_value(), "Content-Type": "application/json", "User-Agent": "airflow-oncall-agent/1.0"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    # ----------------------------------------------------------------- plumbing

    def _gql(self, document: str, variables: dict[str, Any] | None = None, *, mutation: bool = False) -> dict[str, Any]:
        response = send(
            self._client,
            "POST",
            self._url,
            error_cls=LinearError,
            idempotent=not mutation,
            is_transient=_is_rate_limited,
            json={"query": document, "variables": variables or {}},
        )
        body = json_body(response, SERVICE)
        if not isinstance(body, dict):
            raise MalformedResponseError(SERVICE, "GraphQL response is not an object")
        errors = body.get("errors")
        if errors:
            message = errors[0].get("message", "unknown error") if isinstance(errors[0], dict) else str(errors[0])
            raise LinearError(f"GraphQL error: {message}", retriable=_is_rate_limited(response))
        data = body.get("data")
        if not isinstance(data, dict):
            raise MalformedResponseError(SERVICE, "GraphQL response has no 'data'")
        return data

    def _paged_nodes(self, document: str, issue_id: str, connection: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(MAX_PAGES):
            data = self._gql(document, {"id": issue_id, "after": after})
            conn = _dig(data, "issue", connection)
            page = _dig(conn, "nodes")
            if not isinstance(page, list):
                raise MalformedResponseError(SERVICE, f"{connection}.nodes is not a list")
            nodes.extend(page)
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        return nodes

    def _issue_payload(self, data: dict[str, Any], op: str) -> LinearIssue:
        result = _dig(data, op)
        if not result.get("success"):
            raise LinearError(f"{op} reported success=false")
        return parse(LinearIssue, _dig(result, "issue"), SERVICE)

    # ----------------------------------------------------------------- reads

    def check_connection(self) -> dict[str, Any]:
        viewer = self.viewer()
        team = _dig(self._gql(Q_TEAM, {"id": self._team_id}), "team")
        if team is None:
            raise LinearError(f"Team {self._team_id} not found or not accessible")
        return {"viewer": viewer.name or viewer.id, "team": f"{team.get('key')} ({team.get('name')})"}

    def viewer(self) -> LinearUser:
        return parse(LinearUser, _dig(self._gql(Q_VIEWER), "viewer"), SERVICE)

    def find_label_ids(self, names: list[str]) -> dict[str, str]:
        """Map label names to IDs, preferring this team's labels over workspace labels.
        Labels that do not exist are simply absent (decision A7: never auto-created)."""
        if not names:
            return {}
        nodes = _dig(self._gql(Q_LABELS, {"names": names}), "issueLabels", "nodes")
        found: dict[str, str] = {}
        for node in nodes:
            team = (node.get("team") or {}).get("id")
            if team not in (None, self._team_id):
                continue
            if node["name"] not in found or team == self._team_id:
                found[node["name"]] = node["id"]
        return found

    def list_workflow_states(self) -> list[LinearState]:
        nodes = _dig(self._gql(Q_STATES, {"teamId": self._team_id}), "workflowStates", "nodes")
        return [parse(LinearState, n, SERVICE) for n in nodes]

    def find_user(self, identifier: str) -> LinearUser | None:
        if "@" in identifier:
            nodes = _dig(self._gql(Q_USER_BY_EMAIL, {"email": identifier}), "users", "nodes")
            return parse(LinearUser, nodes[0], SERVICE) if nodes else None
        try:
            user = _dig(self._gql(Q_USER_BY_ID, {"id": identifier}), "user")
        except LinearError as exc:
            if "not found" in str(exc).lower() or "entity" in str(exc).lower():
                return None
            raise
        return parse(LinearUser, user, SERVICE) if user else None

    def get_issue(self, issue_id: str) -> LinearIssue:
        issue = _dig(self._gql(Q_ISSUE, {"id": issue_id}), "issue")
        if issue is None:
            raise LinearError(f"Issue {issue_id} not found")
        return parse(LinearIssue, issue, SERVICE)

    def search_issues(self, text: str, limit: int = 20) -> list[LinearIssue]:
        nodes = _dig(self._gql(Q_SEARCH, {"teamId": self._team_id, "text": text, "first": max(1, min(limit, 100))}), "issues", "nodes")
        return [parse(LinearIssue, n, SERVICE) for n in nodes]

    def list_comments(self, issue_id: str) -> list[LinearComment]:
        return [parse(LinearComment, n, SERVICE) for n in self._paged_nodes(Q_COMMENTS, issue_id, "comments")]

    def list_label_events(self, issue_id: str) -> list[LabelEvent]:
        events = [parse(LabelEvent, n, SERVICE) for n in self._paged_nodes(Q_HISTORY, issue_id, "history")]
        return [e for e in events if e.added_label_ids or e.removed_label_ids]

    # ----------------------------------------------------------------- writes

    def create_issue(
        self,
        *,
        title: str,
        description: str,
        priority: int | None = None,
        label_ids: list[str] | None = None,
        assignee_id: str | None = None,
        state_id: str | None = None,
    ) -> LinearIssue:
        payload: dict[str, Any] = {"teamId": self._team_id, "title": title, "description": description}
        if self._settings.project_id:
            payload["projectId"] = self._settings.project_id
        if priority is not None:
            payload["priority"] = priority
        if label_ids:
            payload["labelIds"] = label_ids
        if assignee_id:
            payload["assigneeId"] = assignee_id
        if state_id:
            payload["stateId"] = state_id
        return self._issue_payload(self._gql(M_CREATE, {"input": payload}, mutation=True), "issueCreate")

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        assignee_id: str | None = None,
        state_id: str | None = None,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> LinearIssue:
        payload: dict[str, Any] = {}
        for key, value in (
            ("title", title), ("description", description), ("priority", priority), ("assigneeId", assignee_id),
            ("stateId", state_id), ("addedLabelIds", add_label_ids), ("removedLabelIds", remove_label_ids),
        ):  # fmt: skip
            if value is not None and value != []:
                payload[key] = value
        if not payload:
            return self.get_issue(issue_id)
        return self._issue_payload(self._gql(M_UPDATE, {"id": issue_id, "input": payload}, mutation=True), "issueUpdate")

    def add_comment(self, issue_id: str, body: str) -> LinearComment:
        data = self._gql(M_COMMENT, {"input": {"issueId": issue_id, "body": body}}, mutation=True)
        result = _dig(data, "commentCreate")
        if not result.get("success"):
            raise LinearError("commentCreate reported success=false")
        return parse(LinearComment, _dig(result, "comment"), SERVICE)
