"""In-memory Linear for offline development, tests and approval simulation.

The agent's own API user is ``viewer`` (``user-oncall-bot``). Every label change made
through ``update_issue`` is recorded in the issue history *as the viewer*, exactly as
Linear would, so the approval service can tell agent-made changes from human ones.
Humans are simulated with :meth:`add_label_as` and :meth:`comment_as`.
"""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone
from typing import Any

from ...exceptions import LinearError
from ..linear import LabelEvent, LinearComment, LinearIssue, LinearLabel, LinearState, LinearUser
from .base import FaultInjector

DEFAULT_LABELS = [
    "oncall", "oncall:approved", "oncall:retriable", "oncall:non-retriable", "oncall:uncertain",
    "oncall:import-error", "oncall:awaiting-approval", "oncall:escalated",
]  # fmt: skip

AGENT_USER = LinearUser(id="user-oncall-bot", name="Oncall Agent", email="oncall-bot@example.com")
SAMPLE_USERS = [
    AGENT_USER,
    LinearUser(id="user-alice", name="Alice Example", email="alice@example.com"),
    LinearUser(id="user-bob", name="Bob Example", email="bob@example.com"),
    LinearUser(id="user-mallory", name="Mallory Untrusted", email="mallory@example.com"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MockLinearClient(FaultInjector):
    error_cls = LinearError

    def __init__(self, labels: list[str] | None = None, users: list[LinearUser] | None = None, team_key: str = "ONC") -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self.team_key = team_key
        self.viewer_user = AGENT_USER
        self.users = {u.id: u for u in (users or SAMPLE_USERS)}
        self.labels = {name: LinearLabel(id=f"label-{i}", name=name) for i, name in enumerate(labels if labels is not None else DEFAULT_LABELS, 1)}
        self.states = [
            LinearState(id="state-todo", name="Todo", type="unstarted"),
            LinearState(id="state-progress", name="In Progress", type="started"),
            LinearState(id="state-done", name="Done", type="completed"),
            LinearState(id="state-canceled", name="Canceled", type="canceled"),
        ]
        self.issues: dict[str, LinearIssue] = {}
        self.comments: dict[str, list[LinearComment]] = {}
        self.history: dict[str, list[LabelEvent]] = {}

    def close(self) -> None:
        pass

    # ----------------------------------------------------------------- helpers

    def _label_by_id(self, label_id: str) -> LinearLabel:
        for label in self.labels.values():
            if label.id == label_id:
                return label
        raise LinearError(f"Label {label_id} not found")

    def _state(self, state_id: str | None) -> LinearState:
        for s in self.states:
            if s.id == state_id:
                return s
        if state_id is None:
            return self.states[0]
        raise LinearError(f"State {state_id} not found")

    def _issue(self, issue_id: str) -> LinearIssue:
        if issue_id in self.issues:
            return self.issues[issue_id]
        for issue in self.issues.values():
            if issue.identifier == issue_id:
                return issue
        raise LinearError(f"Issue {issue_id} not found")

    def _record_labels(self, issue_id: str, actor: LinearUser, added: list[str], removed: list[str]) -> None:
        if added or removed:
            self.history.setdefault(issue_id, []).append(
                LabelEvent(createdAt=_now(), actor=actor, addedLabelIds=added, removedLabelIds=removed)
            )

    # ----------------------------------------------------------------- reads

    def check_connection(self) -> dict[str, Any]:
        self._enter("check_connection")
        return {"viewer": self.viewer_user.name, "team": f"{self.team_key} (mock team)"}

    def viewer(self) -> LinearUser:
        self._enter("viewer")
        return self.viewer_user

    def find_label_ids(self, names: list[str]) -> dict[str, str]:
        self._enter("find_label_ids", names=names)
        return {n: self.labels[n].id for n in names if n in self.labels}

    def list_workflow_states(self) -> list[LinearState]:
        self._enter("list_workflow_states")
        return list(self.states)

    def find_user(self, identifier: str) -> LinearUser | None:
        self._enter("find_user", identifier=identifier)
        for user in self.users.values():
            if identifier in (user.id, user.email):
                return user
        return None

    def get_issue(self, issue_id: str) -> LinearIssue:
        self._enter("get_issue", issue_id=issue_id)
        return self._issue(issue_id)

    def search_issues(self, text: str, limit: int = 20) -> list[LinearIssue]:
        self._enter("search_issues", text=text)
        t = text.lower()
        return [i for i in self.issues.values() if t in i.title.lower() or t in (i.description or "").lower()][:limit]

    def list_comments(self, issue_id: str) -> list[LinearComment]:
        self._enter("list_comments", issue_id=issue_id)
        return list(self.comments.get(self._issue(issue_id).id, []))

    def list_label_events(self, issue_id: str) -> list[LabelEvent]:
        self._enter("list_label_events", issue_id=issue_id)
        return list(self.history.get(self._issue(issue_id).id, []))

    # ----------------------------------------------------------------- writes (as the agent)

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
        self._enter("create_issue", title=title)
        with self._lock:
            n = next(self._ids)
            issue = LinearIssue(
                id=f"issue-{n}", identifier=f"{self.team_key}-{n}", title=title, description=description, priority=priority,
                url=f"https://linear.app/mock/issue/{self.team_key}-{n}", state=self._state(state_id),
                assignee=self.users.get(assignee_id) if assignee_id else None,
                labels=[self._label_by_id(i) for i in (label_ids or [])],
            )  # fmt: skip
            self.issues[issue.id] = issue
            self._record_labels(issue.id, self.viewer_user, list(label_ids or []), [])
            return issue

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
        self._enter("update_issue", issue_id=issue_id)
        with self._lock:
            issue = self._issue(issue_id)
            labels = {l.id: l for l in issue.labels}
            added = [i for i in (add_label_ids or []) if i not in labels]
            removed = [i for i in (remove_label_ids or []) if i in labels]
            for i in added:
                labels[i] = self._label_by_id(i)
            for i in removed:
                labels.pop(i)
            update: dict[str, Any] = {"labels": list(labels.values())}
            if title is not None:
                update["title"] = title
            if description is not None:
                update["description"] = description
            if priority is not None:
                update["priority"] = priority
            if assignee_id is not None:
                update["assignee"] = self.users.get(assignee_id)
            if state_id is not None:
                update["state"] = self._state(state_id)
            issue = issue.model_copy(update=update)
            self.issues[issue.id] = issue
            self._record_labels(issue.id, self.viewer_user, added, removed)
            return issue

    def add_comment(self, issue_id: str, body: str) -> LinearComment:
        self._enter("add_comment", issue_id=issue_id)
        return self._comment(issue_id, body, self.viewer_user)

    def _comment(self, issue_id: str, body: str, user: LinearUser) -> LinearComment:
        with self._lock:
            issue = self._issue(issue_id)
            comment = LinearComment(id=f"comment-{next(self._ids)}", body=body, createdAt=_now(), user=user)
            self.comments.setdefault(issue.id, []).append(comment)
            return comment

    # ----------------------------------------------------------------- humans (tests / simulation)

    def add_label_as(self, issue_id: str, label_name: str, user_id: str) -> None:
        """A human adds a label in the Linear UI."""
        with self._lock:
            issue = self._issue(issue_id)
            label = self.labels[label_name]
            if label.id not in {l.id for l in issue.labels}:
                self.issues[issue.id] = issue.model_copy(update={"labels": [*issue.labels, label]})
            self._record_labels(issue.id, self.users[user_id], [label.id], [])

    def comment_as(self, issue_id: str, body: str, user_id: str) -> LinearComment:
        """A human writes a comment in the Linear UI."""
        return self._comment(issue_id, body, self.users[user_id])
