"""Search tools (2)."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..schemas import NonEmptyStr, Page, PerPage, page_result
from ..state import AppState
from .common import github_tool


def register(mcp: MCPServer, app: AppState) -> None:
    @github_tool(
        mcp,
        "Search issues and pull requests across GitHub using GitHub search syntax, e.g. "
        "'repo:owner/name is:pr is:open label:bug'. Add is:issue or is:pr to narrow results.",
    )
    async def search_issues(
        query: Annotated[NonEmptyStr, Field(description="GitHub issue search query (non-empty).")],
        ctx: Context,
        sort: Literal["comments", "reactions", "created", "updated", "interactions"] | None = None,
        order: Literal["asc", "desc"] = "desc",
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        params = {"q": query, "sort": sort, "order": order if sort else None, "page": page, "per_page": per_page}
        async with app.client_for(ctx.headers) as gh:
            data = await gh.get_json("search/issues", params)
        return page_result(data.get("items", []), page, per_page, total_count=data.get("total_count"),
                           incomplete_results=data.get("incomplete_results"))

    @github_tool(
        mcp,
        "Search source code using GitHub code search syntax, e.g. 'httpx.AsyncClient repo:owner/name language:python'.",
    )
    async def search_code(
        query: Annotated[NonEmptyStr, Field(description="GitHub code search query (non-empty).")],
        ctx: Context,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            data = await gh.get_json("search/code", {"q": query, "page": page, "per_page": per_page})
        items = [
            {"name": i.get("name"), "path": i.get("path"), "sha": i.get("sha"), "html_url": i.get("html_url"),
             "repository": (i.get("repository") or {}).get("full_name"), "score": i.get("score")}
            for i in data.get("items", [])
        ]
        return page_result(items, page, per_page, total_count=data.get("total_count"),
                           incomplete_results=data.get("incomplete_results"))
