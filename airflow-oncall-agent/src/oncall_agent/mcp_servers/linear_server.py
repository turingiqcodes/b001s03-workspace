"""``oncall-linear-mcp`` — controlled Linear incident tools over MCP (SRS §8.2, §8.3).

Read tools: search issues, get approval information.
Write tools (disabled unless ``ONCALL_MCP_ALLOW_LINEAR_WRITES=true``): create the issue
for an agent incident, update an issue, add a comment, update labels, resolve an
incident.

Approval safety: these tools act as the agent's own Linear user, whose label changes
and comments are never accepted as approvals. In addition the tools refuse to add the
approval label or post an approval command, so an MCP client cannot even appear to
approve a restricted action.

Run: ``oncall-linear-mcp`` (stdio transport). Logs go to stderr only.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..config import load_settings
from ..exceptions import ApprovalError, ConfigError, WriteDisabledError
from ..logging_config import setup_logging
from ..models import Incident
from .common import ISSUE_ID, LABEL, InvalidArgument, NotFound, Runtime, bounded_int, bounded_str, ident, tool_call

SERVER = "oncall-linear-mcp"


class LinearTools:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.linear = rt.integrations.linear
        self.approval_label = rt.settings.linear.labels.approved
        self.approve_command = rt.settings.approval.comment_command.strip().lower()

    def _call(self, tool: str, args: dict[str, Any], fn: Callable[[], Any], *, write: bool = False) -> dict[str, Any]:
        return tool_call(self.rt, SERVER, tool, args, fn, write=write)

    def _require_writes(self) -> None:
        if not self.rt.settings.mcp.allow_linear_writes:
            raise WriteDisabledError("Linear write tools are disabled (ONCALL_MCP_ALLOW_LINEAR_WRITES=false)")

    def _incident(self, incident_id: Any) -> Incident:
        incident = self.rt.store.get_incident(bounded_int("incident_id", incident_id, 1, 2**31))
        if incident is None:
            raise NotFound(f"incident {incident_id} does not exist")
        return incident

    @staticmethod
    def _issue(issue) -> dict[str, Any]:
        return {"id": issue.id, "identifier": issue.identifier, "title": issue.title, "url": issue.url, "priority": issue.priority,
                "state": issue.state.name if issue.state else None, "labels": sorted(issue.label_names)}  # fmt: skip

    # ----------------------------------------------------------------- reads

    def search_issues(self, query: str, limit: int = 10) -> dict[str, Any]:
        def body() -> Any:
            return [self._issue(i) for i in self.linear.search_issues(bounded_str("query", query, 200), bounded_int("limit", limit, 1, 50))]

        return self._call("search_issues", {"query": query, "limit": limit}, body)

    def get_approval_info(self, incident_id: int) -> dict[str, Any]:
        def body() -> Any:
            incident = self._incident(incident_id)
            return {
                "incident_status": incident.status.value,
                "linear_issue": incident.linear_issue_identifier,
                "how_to_approve": f"A trusted approver adds the label '{self.approval_label}' or comments '{self.rt.settings.approval.comment_command}' in Linear.",
                "trusted_approvers_configured": len(self.rt.settings.approval.trusted_approvers),
                "approvals": [a.model_dump(mode="json") for a in self.rt.store.list_approvals(incident.id)],
            }

        return self._call("get_approval_info", {"incident_id": incident_id}, body)

    # ----------------------------------------------------------------- writes

    def create_incident(self, incident_id: int) -> dict[str, Any]:
        def body() -> Any:
            self._require_writes()
            incident = self._incident(incident_id)
            if incident.classification is None:
                raise InvalidArgument("the incident has not been analysed yet")
            if incident.linear_issue_id is None:
                incident = self.rt.linear_sync.sync(incident)
            return {"linear_issue_id": incident.linear_issue_id, "identifier": incident.linear_issue_identifier}

        return self._call("create_incident", {"incident_id": incident_id}, body, write=True)

    def update_issue(self, issue_id: str, title: str | None = None, description: str | None = None, priority: int | None = None) -> dict[str, Any]:
        def body() -> Any:
            self._require_writes()
            issue = self.linear.update_issue(
                ident("issue_id", issue_id, ISSUE_ID),
                title=bounded_str("title", title, 255) if title is not None else None,
                description=bounded_str("description", description, 50000) if description is not None else None,
                priority=bounded_int("priority", priority, 0, 4) if priority is not None else None,
            )
            return self._issue(issue)

        return self._call("update_issue", {"issue_id": issue_id, "title": title, "priority": priority}, body, write=True)

    def add_comment(self, issue_id: str, body_text: str) -> dict[str, Any]:
        def body() -> Any:
            self._require_writes()
            text = bounded_str("body", body_text, 20000)
            first = next((line.strip().lower() for line in text.splitlines() if line.strip()), "")
            if first == self.approve_command:
                raise ApprovalError("approval commands cannot be posted through MCP; a trusted human must approve in Linear")
            return {"comment_id": self.linear.add_comment(ident("issue_id", issue_id, ISSUE_ID), text).id}

        return self._call("add_comment", {"issue_id": issue_id, "body": body_text}, body, write=True)

    def update_labels(self, issue_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
        def body() -> Any:
            self._require_writes()
            add_names = [ident("label", n, LABEL) for n in (add or [])]
            remove_names = [ident("label", n, LABEL) for n in (remove or [])]
            if len(add_names) + len(remove_names) > 20:
                raise InvalidArgument("at most 20 label changes per call")
            if self.approval_label in add_names:
                raise ApprovalError(f"the '{self.approval_label}' label cannot be added through MCP; a trusted human must approve in Linear")
            ids = self.linear.find_label_ids(add_names + remove_names)
            unknown = [n for n in add_names + remove_names if n not in ids]
            if unknown:
                raise InvalidArgument(f"unknown label(s): {', '.join(unknown)}")
            issue = self.linear.update_issue(ident("issue_id", issue_id, ISSUE_ID), add_label_ids=[ids[n] for n in add_names], remove_label_ids=[ids[n] for n in remove_names])
            return self._issue(issue)

        return self._call("update_labels", {"issue_id": issue_id, "add": add, "remove": remove}, body, write=True)

    def resolve_incident(self, incident_id: int, reason: str) -> dict[str, Any]:
        def body() -> Any:
            self._require_writes()
            incident = self._incident(incident_id)
            self.rt.recovery.resolve(incident, f"resolved through MCP: {bounded_str('reason', reason, 500)}")
            synced = self.rt.linear_sync.sync(self.rt.store.get_incident(incident.id))  # type: ignore[arg-type]
            return {"status": synced.status.value, "linear_issue": synced.linear_issue_identifier}

        return self._call("resolve_incident", {"incident_id": incident_id, "reason": reason}, body, write=True)


def build_server(rt: Runtime) -> MCPServer:
    tools = LinearTools(rt)
    server = MCPServer(SERVER, instructions="Manage on-call incidents in Linear. These tools can never approve restricted actions.")

    @server.tool(description="Search the team's Linear issues by text.")
    def search_issues(query: str, limit: int = 10) -> dict[str, Any]:
        return tools.search_issues(query, limit)

    @server.tool(description="Approval requests and their status for an agent incident.")
    def get_approval_info(incident_id: int) -> dict[str, Any]:
        return tools.get_approval_info(incident_id)

    @server.tool(description="Create (or return) the Linear issue for an analysed agent incident. Requires Linear writes.")
    def create_incident(incident_id: int) -> dict[str, Any]:
        return tools.create_incident(incident_id)

    @server.tool(description="Update an issue's title, description or priority (0-4). Requires Linear writes.")
    def update_issue(issue_id: str, title: str | None = None, description: str | None = None, priority: int | None = None) -> dict[str, Any]:
        return tools.update_issue(issue_id, title, description, priority)

    @server.tool(description="Add a comment to an issue. Cannot post approval commands. Requires Linear writes.")
    def add_comment(issue_id: str, body: str) -> dict[str, Any]:
        return tools.add_comment(issue_id, body)

    @server.tool(description="Add/remove labels by name. Cannot add the approval label. Requires Linear writes.")
    def update_labels(issue_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
        return tools.update_labels(issue_id, add, remove)

    @server.tool(description="Mark an agent incident resolved and update its Linear issue. Requires Linear writes.")
    def resolve_incident(incident_id: int, reason: str) -> dict[str, Any]:
        return tools.resolve_incident(incident_id, reason)

    return server


def main() -> int:  # pragma: no cover - process entry point
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3
    setup_logging(settings.logging.level, "json", settings.logging.dir, secrets=settings.secret_values())
    rt = Runtime.build(settings)
    try:
        build_server(rt).run("stdio")
    finally:
        rt.close()
    return 0
