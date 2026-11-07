"""Real Linear client against recorded GraphQL responses (FR-068/069/074..077;
"Linear unavailable", "Linear issue update failure", "Malformed API responses")."""
from __future__ import annotations

import pytest

from oncall_agent.config import LinearSettings
from oncall_agent.exceptions import LinearError, MalformedResponseError
from oncall_agent.integrations.linear import HttpLinearClient, LinearClient

from .fakes import FakeServer, raise_timeout

pytestmark = pytest.mark.integration

PATH = "/graphql"
ISSUE = {
    "id": "issue-uuid", "identifier": "ONC-12", "title": "[oncall] sales_etl.extract_orders failed", "url": "https://linear.app/x/ONC-12",
    "priority": 4, "description": "fingerprint: abc", "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
    "assignee": None, "labels": {"nodes": [{"id": "l1", "name": "oncall"}]},
}  # fmt: skip


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("oncall_agent.integrations.http.time.sleep", lambda s: None)


def client(server: FakeServer, **overrides) -> HttpLinearClient:
    settings = LinearSettings(**{"api_key": "lin_api_" + "k" * 32, "team_id": "team-1", **overrides})
    return HttpLinearClient(settings, transport=server.transport(), url="https://api.linear.test/graphql")


def gql(*replies):
    return FakeServer().on("POST", PATH, *replies)


def sent(server: FakeServer, i: int = 0) -> dict:
    return server.body(server.requests[i])


def test_api_key_is_sent_without_bearer_prefix_and_viewer_parses():
    server = gql((200, {"data": {"viewer": {"id": "u1", "name": "Oncall Bot", "email": "bot@example.com"}}}))
    c = client(server)
    assert c.viewer().id == "u1"
    assert server.requests[0].headers["Authorization"] == "lin_api_" + "k" * 32
    assert isinstance(c, LinearClient)


def test_check_connection_reports_missing_team():
    server = gql((200, {"data": {"viewer": {"id": "u1", "name": "Bot"}}}), (200, {"data": {"team": None}}))
    with pytest.raises(LinearError, match="not found"):
        client(server).check_connection()


def test_create_issue_sends_team_project_priority_labels_and_assignee():  # FR-074/076
    server = gql((200, {"data": {"issueCreate": {"success": True, "issue": ISSUE}}}))
    issue = client(server, project_id="proj-1").create_issue(
        title="t", description="d", priority=2, label_ids=["l1", "l2"], assignee_id="user-alice", state_id="s1"
    )
    assert issue.identifier == "ONC-12" and issue.label_names == {"oncall"}
    payload = sent(server)["variables"]["input"]
    assert payload == {"teamId": "team-1", "projectId": "proj-1", "title": "t", "description": "d", "priority": 2,
                       "labelIds": ["l1", "l2"], "assigneeId": "user-alice", "stateId": "s1"}  # fmt: skip
    assert "issueCreate" in sent(server)["query"]


def test_update_issue_adds_and_removes_labels():  # FR-075/076
    server = gql((200, {"data": {"issueUpdate": {"success": True, "issue": ISSUE}}}))
    client(server).update_issue("issue-uuid", priority=3, add_label_ids=["l9"], remove_label_ids=["l1"])
    assert sent(server)["variables"] == {"id": "issue-uuid", "input": {"priority": 3, "addedLabelIds": ["l9"], "removedLabelIds": ["l1"]}}


def test_update_with_nothing_to_change_just_reads_the_issue():
    server = gql((200, {"data": {"issue": ISSUE}}))
    assert client(server).update_issue("issue-uuid").id == "issue-uuid"
    assert "issueUpdate" not in sent(server)["query"]


def test_add_comment():  # FR-077
    server = gql((200, {"data": {"commentCreate": {"success": True, "comment": {"id": "c1", "body": "hi", "createdAt": "2026-11-05T06:00:00Z", "user": {"id": "u1"}}}}}))
    assert client(server).add_comment("issue-uuid", "hi").body == "hi"
    assert sent(server)["variables"]["input"] == {"issueId": "issue-uuid", "body": "hi"}


def test_label_lookup_prefers_team_labels_and_ignores_other_teams():
    nodes = [
        {"id": "ws-approved", "name": "oncall:approved", "team": None},
        {"id": "team-approved", "name": "oncall:approved", "team": {"id": "team-1"}},
        {"id": "other-team", "name": "oncall", "team": {"id": "team-2"}},
    ]
    server = gql((200, {"data": {"issueLabels": {"nodes": nodes}}}))
    assert client(server).find_label_ids(["oncall:approved", "oncall"]) == {"oncall:approved": "team-approved"}


def test_label_history_is_paginated_and_filtered():  # FR-068 evidence
    page1 = {"nodes": [{"createdAt": "2026-11-05T06:00:00Z", "actor": {"id": "user-alice", "email": "alice@example.com"}, "addedLabelIds": ["l-approved"], "removedLabelIds": None},
                       {"createdAt": "2026-11-05T06:01:00Z", "actor": None, "addedLabelIds": None, "removedLabelIds": None}],
             "pageInfo": {"hasNextPage": True, "endCursor": "cur1"}}  # fmt: skip
    page2 = {"nodes": [{"createdAt": "2026-11-05T06:02:00Z", "actor": {"id": "user-bob"}, "addedLabelIds": [], "removedLabelIds": ["l-approved"]}], "pageInfo": {"hasNextPage": False}}
    server = gql((200, {"data": {"issue": {"history": page1}}}), (200, {"data": {"issue": {"history": page2}}}))
    events = client(server).list_label_events("issue-uuid")
    assert [e.actor.id for e in events] == ["user-alice", "user-bob"]  # the no-label entry is dropped
    assert events[0].added_label_ids == ["l-approved"] and events[1].removed_label_ids == ["l-approved"]
    assert sent(server, 1)["variables"]["after"] == "cur1"


def test_comments_include_author():  # FR-069 evidence
    server = gql((200, {"data": {"issue": {"comments": {"nodes": [{"id": "c1", "body": "/approve", "createdAt": "2026-11-05T06:00:00Z", "user": {"id": "user-alice", "email": "alice@example.com"}}], "pageInfo": {"hasNextPage": False}}}}}))
    comments = client(server).list_comments("issue-uuid")
    assert comments[0].user.email == "alice@example.com" and comments[0].body == "/approve"


def test_find_user_by_email_and_by_id():
    server = gql((200, {"data": {"users": {"nodes": [{"id": "user-alice", "email": "alice@example.com"}]}}}), (200, {"data": {"user": {"id": "user-bob"}}}))
    c = client(server)
    assert c.find_user("alice@example.com").id == "user-alice"
    assert c.find_user("user-bob").id == "user-bob"


def test_search_is_scoped_to_team():
    server = gql((200, {"data": {"issues": {"nodes": [ISSUE]}}}))
    assert client(server).search_issues("abc")[0].identifier == "ONC-12"
    assert sent(server)["variables"] == {"teamId": "team-1", "text": "abc", "first": 20}


# --------------------------------------------------------------------------- failures


def test_graphql_errors_raise_linear_error():
    server = gql((200, {"errors": [{"message": "Entity not found: Issue"}], "data": None}))
    with pytest.raises(LinearError, match="Entity not found"):
        client(server).get_issue("missing")


def test_graphql_400_is_not_retried():
    server = gql((400, {"errors": [{"message": "Argument Validation Error"}]}))
    with pytest.raises(LinearError, match="Argument Validation Error") as info:
        client(server).viewer()
    assert not info.value.retriable and len(server.requests) == 1


def test_rate_limited_query_is_retried():
    server = gql((400, {"errors": [{"message": "Rate limit exceeded", "extensions": {"code": "RATELIMITED"}}]}), (200, {"data": {"viewer": {"id": "u1"}}}))
    assert client(server).viewer().id == "u1"
    assert len(server.requests) == 2


def test_failed_mutation_is_not_repeated():  # "Linear issue update failure"
    server = gql((500, {"errors": [{"message": "internal"}]}))
    with pytest.raises(LinearError) as info:
        client(server).update_issue("issue-uuid", priority=1)
    assert info.value.retriable and len(server.requests) == 1


def test_mutation_reporting_success_false_raises():
    server = gql((200, {"data": {"issueUpdate": {"success": False, "issue": None}}}))
    with pytest.raises(LinearError, match="success=false"):
        client(server).update_issue("issue-uuid", priority=1)


def test_linear_unavailable_raises_retriable_error():
    server = gql(raise_timeout)
    with pytest.raises(LinearError) as info:
        client(server).viewer()
    assert info.value.retriable and len(server.requests) == 3


@pytest.mark.parametrize("reply", [(200, {"data": {}}), (200, {"nothing": 1}), (200, {"data": {"issue": {"id": "x"}}}), (200, [1, 2])])
def test_malformed_graphql_responses_are_rejected(reply):
    server = gql(reply)
    with pytest.raises((MalformedResponseError, LinearError)):
        client(server).get_issue("x")


def test_missing_configuration_is_rejected():
    with pytest.raises(LinearError):
        HttpLinearClient(LinearSettings(api_key=None, team_id="t"))
