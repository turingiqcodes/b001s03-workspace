"""Issue tools, search tools, and pagination boundaries."""

import httpx
import pytest

from .conftest import call, error_text

ISSUES = "/repos/o/r/issues"


async def test_get_issue(gh):
    gh.add("GET", ISSUES + "/3", {"number": 3})
    assert (await call(gh, "get_issue", {"owner": "o", "repo": "r", "issue_number": 3})).structured_content["number"] == 3


async def test_list_issues_hides_pull_requests_by_default(gh):
    gh.add("GET", ISSUES, [{"number": 1}, {"number": 2, "pull_request": {"url": "x"}}])
    r = await call(gh, "list_issues", {"owner": "o", "repo": "r", "state": "all", "labels": ["bug", "p1"]})
    sc = r.structured_content
    assert [i["number"] for i in sc["items"]] == [1] and sc["filtered_pull_requests"] == 1
    params = gh.requests[0].url.params
    assert params["state"] == "all" and params["labels"] == "bug,p1"


async def test_list_issues_can_include_pull_requests(gh):
    gh.add("GET", ISSUES, [{"number": 1}, {"number": 2, "pull_request": {}}])
    r = await call(gh, "list_issues", {"owner": "o", "repo": "r", "include_pull_requests": True})
    assert r.structured_content["count"] == 2 and r.structured_content["filtered_pull_requests"] == 0


async def test_list_issues_invalid_state(gh):
    assert (await call(gh, "list_issues", {"owner": "o", "repo": "r", "state": "merged"})).is_error


async def test_create_issue(gh):
    gh.add("POST", ISSUES, httpx.Response(201, json={"number": 11, "html_url": "u"}))
    r = await call(gh, "create_issue", {"owner": "o", "repo": "r", "title": "  Crash  ", "labels": ["bug"]})
    assert r.structured_content["number"] == 11
    assert gh.body() == {"title": "Crash", "labels": ["bug"]}


@pytest.mark.parametrize("title", ["", "   "])
async def test_create_issue_rejects_empty_title(gh, title):
    r = await call(gh, "create_issue", {"owner": "o", "repo": "r", "title": title})
    assert r.is_error and gh.requests == []


async def test_add_labels(gh):
    gh.add("POST", ISSUES + "/3/labels", [{"name": "old"}, {"name": "bug"}])
    r = await call(gh, "add_labels", {"owner": "o", "repo": "r", "issue_number": 3, "labels": ["bug"]})
    assert r.structured_content["labels"] == ["old", "bug"] and gh.body() == {"labels": ["bug"]}


@pytest.mark.parametrize("labels", [[], [""], ["ok", "  "]])
async def test_add_labels_rejects_empty(gh, labels):
    r = await call(gh, "add_labels", {"owner": "o", "repo": "r", "issue_number": 3, "labels": labels})
    assert r.is_error and gh.requests == []


async def test_update_issue_replacement_semantics(gh):
    gh.add("PATCH", ISSUES + "/3", {"number": 3})
    r = await call(gh, "update_issue", {"owner": "o", "repo": "r", "issue_number": 3, "labels": ["a"], "assignees": []})
    sc = r.structured_content
    assert gh.body() == {"labels": ["a"], "assignees": []}  # [] clears assignees
    assert sc["replaced_fields"] == ["labels", "assignees"] and "replaced" in sc["replacement_note"]

    r = await call(gh, "update_issue", {"owner": "o", "repo": "r", "issue_number": 3, "title": "T"})
    assert gh.body() == {"title": "T"}  # labels/assignees omitted -> untouched
    assert r.structured_content["replaced_fields"] == []


async def test_update_issue_validation(gh):
    assert "at least one field" in error_text(
        await call(gh, "update_issue", {"owner": "o", "repo": "r", "issue_number": 3}))
    assert (await call(gh, "update_issue", {"owner": "o", "repo": "r", "issue_number": 3, "title": " "})).is_error


@pytest.mark.parametrize("reason", ["completed", "not_planned"])
async def test_close_issue_reasons(gh, reason):
    gh.add("PATCH", ISSUES + "/3", {"state": "closed", "state_reason": reason})
    r = await call(gh, "close_issue", {"owner": "o", "repo": "r", "issue_number": 3, "reason": reason})
    assert gh.body() == {"state": "closed", "state_reason": reason}
    assert r.structured_content["state_reason"] == reason


async def test_close_issue_rejects_unknown_reason(gh):
    assert (await call(gh, "close_issue", {"owner": "o", "repo": "r", "issue_number": 3, "reason": "duplicate"})).is_error


# ------------------------------------------------------------------ search
async def test_search_issues(gh):
    gh.add("GET", "/search/issues", {"total_count": 1, "incomplete_results": False, "items": [{"number": 1}]})
    r = await call(gh, "search_issues", {"query": "repo:o/r is:pr is:open", "sort": "updated", "order": "asc"})
    assert r.structured_content["total_count"] == 1
    params = gh.requests[0].url.params
    assert (params["q"], params["sort"], params["order"]) == ("repo:o/r is:pr is:open", "updated", "asc")


async def test_search_code(gh):
    gh.add("GET", "/search/code", {"total_count": 1, "items": [
        {"name": "a.py", "path": "src/a.py", "sha": "s", "html_url": "u", "repository": {"full_name": "o/r"}, "score": 1}]})
    r = await call(gh, "search_code", {"query": "AsyncClient language:python"})
    assert r.structured_content["items"][0]["repository"] == "o/r"


@pytest.mark.parametrize("tool", ["search_issues", "search_code"])
@pytest.mark.parametrize("query", ["", "   "])
async def test_search_rejects_empty_query(gh, tool, query):
    r = await call(gh, tool, {"query": query})
    assert r.is_error and gh.requests == []


# ------------------------------------------------------------------ pagination
@pytest.mark.parametrize("page,per_page", [(0, 30), (-1, 30), (1, 0), (1, 101)])
async def test_pagination_rejects_out_of_range(gh, page, per_page):
    r = await call(gh, "list_issues", {"owner": "o", "repo": "r", "page": page, "per_page": per_page})
    assert r.is_error and gh.requests == []


@pytest.mark.parametrize("per_page", [1, 100])
async def test_pagination_accepts_boundaries(gh, per_page):
    gh.add("GET", "/search/code", {"items": []})
    r = await call(gh, "search_code", {"query": "x", "page": 1, "per_page": per_page})
    assert not r.is_error and gh.requests[0].url.params["per_page"] == str(per_page)


async def test_pagination_defaults(gh):
    gh.add("GET", ISSUES, [])
    r = await call(gh, "list_issues", {"owner": "o", "repo": "r"})
    params = gh.requests[0].url.params
    assert (params["page"], params["per_page"]) == ("1", "30")
    assert r.structured_content["page"] == 1 and r.structured_content["per_page"] == 30
