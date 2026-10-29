"""Tool discovery, names, descriptions, input schemas, and structured output."""

from mcp.types import TextContent

from github_mcp.tools import ALL_TOOLS, WRITE_TOOLS

from .conftest import connect

EXPECTED_TOOLS = {
    # Repository (5)
    "get_repository", "list_repository_branches", "list_repository_files", "get_file_contents",
    "get_repository_commits",
    # Pull requests (11 names, although the spec heading says 10)
    "get_pull_request", "get_pull_request_diff", "list_pull_request_files", "list_pull_request_comments",
    "list_pull_request_reviews", "create_pull_request_comment", "create_pull_request_review",
    "submit_pull_request_review", "create_pull_request", "update_pull_request", "merge_pull_request",
    # CI/CD (4)
    "list_workflow_runs", "get_workflow_run", "list_workflow_run_jobs", "get_workflow_job_logs",
    # Issues (6 names, although the spec heading says 7)
    "get_issue", "list_issues", "create_issue", "add_labels", "update_issue", "close_issue",
    # Search (2)
    "search_issues", "search_code",
}


async def list_tools(gh):
    async with connect(gh) as client:
        return {t.name: t for t in (await client.list_tools()).tools}


def test_inventory_constants():
    assert len(EXPECTED_TOOLS) == 28
    assert set(ALL_TOOLS) == EXPECTED_TOOLS and len(ALL_TOOLS) == 28
    assert len(WRITE_TOOLS) == 10 and set(WRITE_TOOLS) <= EXPECTED_TOOLS


async def test_exactly_28_tools_registered_with_exact_names(gh):
    tools = await list_tools(gh)
    assert set(tools) == EXPECTED_TOOLS
    assert len(tools) == 28


async def test_every_tool_has_description_input_and_output_schema(gh):
    for name, tool in (await list_tools(gh)).items():
        assert tool.description and len(tool.description) > 20, name
        assert tool.input_schema["type"] == "object", name
        assert "ctx" not in tool.input_schema.get("properties", {}), name
        assert tool.output_schema is not None and tool.output_schema["type"] == "object", name


async def test_write_tools_expose_dry_run_and_hints(gh):
    tools = await list_tools(gh)
    for name, tool in tools.items():
        has_dry_run = "dry_run" in tool.input_schema["properties"]
        assert has_dry_run == (name in WRITE_TOOLS), name
        assert tool.annotations.read_only_hint == (name not in WRITE_TOOLS), name
    assert tools["merge_pull_request"].annotations.destructive_hint is True


async def test_pagination_bounds_published_in_schema(gh):
    props = (await list_tools(gh))["list_issues"].input_schema["properties"]
    assert props["page"]["minimum"] == 1 and props["page"]["default"] == 1
    assert props["per_page"]["minimum"] == 1 and props["per_page"]["maximum"] == 100
    assert props["per_page"]["default"] == 30


async def test_enum_schemas(gh):
    tools = await list_tools(gh)
    assert tools["merge_pull_request"].input_schema["properties"]["merge_method"]["enum"] == [
        "merge", "squash", "rebase"]
    assert tools["close_issue"].input_schema["properties"]["reason"]["enum"] == ["completed", "not_planned"]


async def test_structured_output_returned(gh):
    gh.add("GET", "/repos/octo/hello", {"full_name": "octo/hello", "default_branch": "main"})
    async with connect(gh) as client:
        result = await client.call_tool("get_repository", {"owner": "octo", "repo": "hello"})
    assert not result.is_error
    assert result.structured_content["full_name"] == "octo/hello"
    assert isinstance(result.content[0], TextContent)
