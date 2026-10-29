"""Repository tools (5)."""

import base64
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..schemas import Owner, Page, PerPage, Ref, Repo, page_result, truncate_head
from ..state import AppState, encode_file_path, repo_path, seg
from .common import github_tool

MAX_FILE_BYTES = 1024 * 1024  # 1 MB
DEFAULT_MAX_ENTRIES = 1_000
MAX_ENTRIES_LIMIT = 10_000


def _entry(path: str, type_: str, size: int | None, sha: str | None) -> dict[str, Any]:
    return {"path": path, "type": type_, "size": size, "sha": sha}


def register(mcp: MCPServer, app: AppState) -> None:
    @github_tool(mcp, "Get repository metadata: description, visibility, default branch, topics, counts, URLs.")
    async def get_repository(owner: Owner, repo: Repo, ctx: Context) -> dict[str, Any]:
        async with app.client_for(ctx.headers) as gh:
            return await gh.get_json(repo_path(owner, repo))

    @github_tool(mcp, "List branches of a repository, optionally only protected ones.")
    async def list_repository_branches(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        protected: Annotated[bool | None, Field(description="Only protected (true) or unprotected (false) branches.")] = None,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        params = {"page": page, "per_page": per_page, "protected": None if protected is None else str(protected).lower()}
        async with app.client_for(ctx.headers) as gh:
            items = await gh.get_json(repo_path(owner, repo, "branches"), params)
        return page_result(items, page, per_page)

    @github_tool(
        mcp,
        "List files and directories at a path. With recursive=true, lists every file under the path using the git "
        "tree API, capped at max_entries (default 1000, max 10000) with a `truncated` flag.",
    )
    async def list_repository_files(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        path: Annotated[str, Field(description="Directory path; empty for the repository root.")] = "",
        ref: Ref = None,
        recursive: Annotated[bool, Field(description="List all descendants, not just direct children.")] = False,
        max_entries: Annotated[
            int, Field(ge=1, le=MAX_ENTRIES_LIMIT, description="Maximum entries returned when recursive.")
        ] = DEFAULT_MAX_ENTRIES,
    ) -> dict[str, Any]:
        clean = encode_file_path(path) if path.strip("/ ") else ""
        async with app.client_for(ctx.headers) as gh:
            if not recursive:
                data = await gh.get_json(repo_path(owner, repo, "contents", clean).rstrip("/"), {"ref": ref})
                if isinstance(data, dict):
                    raise ValueError(
                        f"'{path}' is a file, not a directory. Use get_file_contents to read it."
                    )
                entries = [_entry(e["path"], e["type"], e.get("size"), e.get("sha")) for e in data]
                return {"path": clean, "ref": ref, "recursive": False, "entries": entries,
                        "count": len(entries), "truncated": False}

            tree_ref = ref or (await gh.get_json(repo_path(owner, repo)))["default_branch"]
            data = await gh.get_json(repo_path(owner, repo, "git", "trees", seg(tree_ref)), {"recursive": "1"})
        prefix = clean + "/" if clean else ""
        entries = [
            _entry(e["path"], "dir" if e["type"] == "tree" else "file" if e["type"] == "blob" else e["type"],
                   e.get("size"), e.get("sha"))
            for e in data.get("tree", [])
            if e["path"].startswith(prefix)
        ]
        if clean and not entries:
            raise ValueError(f"No directory '{path}' found at ref '{tree_ref}'.")
        truncated = len(entries) > max_entries or bool(data.get("truncated"))
        return {
            "path": clean,
            "ref": tree_ref,
            "recursive": True,
            "entries": entries[:max_entries],
            "count": min(len(entries), max_entries),
            "total_found": len(entries),
            "truncated": truncated,
            "github_tree_truncated": bool(data.get("truncated")),
        }

    @github_tool(
        mcp,
        "Read a file's UTF-8 text content. Files over 1 MB and binary files return content=null with a `reason`. "
        "Long text is truncated to the server's character limit. Directories are rejected: use list_repository_files.",
    )
    async def get_file_contents(
        owner: Owner,
        repo: Repo,
        path: Annotated[str, Field(min_length=1, description="File path relative to the repository root.")],
        ctx: Context,
        ref: Ref = None,
    ) -> dict[str, Any]:
        clean = encode_file_path(path)
        if not clean:
            raise ValueError("path must point to a file. Use list_repository_files to list the repository root.")
        async with app.client_for(ctx.headers) as gh:
            data = await gh.get_json(repo_path(owner, repo, "contents", clean), {"ref": ref})
        if isinstance(data, list):
            raise ValueError(
                f"'{path}' is a directory, not a file. Use list_repository_files to list its contents."
            )
        result: dict[str, Any] = {
            "path": data.get("path"),
            "name": data.get("name"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "type": data.get("type"),
            "html_url": data.get("html_url"),
            "encoding": None,
            "content": None,
            "truncated": False,
            "reason": None,
        }
        size = data.get("size") or 0
        if data.get("type") != "file":
            result["reason"] = f"Entry type is '{data.get('type')}', not a regular file."
            return result
        if size > MAX_FILE_BYTES:
            result["reason"] = f"File is {size} bytes, larger than the 1 MB limit; content not returned."
            return result
        if data.get("encoding") != "base64" or data.get("content") is None:
            result["reason"] = "GitHub did not return inline content for this file."
            return result
        raw = base64.b64decode(data["content"])
        try:
            if b"\x00" in raw:
                raise UnicodeDecodeError("utf-8", raw, 0, 1, "NUL byte")
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            result["reason"] = "File appears to be binary (not valid UTF-8 text); content not returned."
            return result
        text, truncated = truncate_head(text, app.settings.max_text_chars)
        result.update(encoding="utf-8", content=text, truncated=truncated)
        if truncated:
            result["reason"] = f"Content truncated to the first {app.settings.max_text_chars} characters."
        return result

    @github_tool(mcp, "List commits, newest first, optionally filtered by branch/SHA, path, author, and date range.")
    async def get_repository_commits(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        sha: Annotated[str | None, Field(description="Branch name or commit SHA to start from.")] = None,
        path: Annotated[str | None, Field(description="Only commits touching this path.")] = None,
        author: Annotated[str | None, Field(description="GitHub login or email address.")] = None,
        since: Annotated[str | None, Field(description="ISO 8601 timestamp (inclusive).")] = None,
        until: Annotated[str | None, Field(description="ISO 8601 timestamp (inclusive).")] = None,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        params = {"sha": sha, "path": path, "author": author, "since": since, "until": until,
                  "page": page, "per_page": per_page}
        async with app.client_for(ctx.headers) as gh:
            items = await gh.get_json(repo_path(owner, repo, "commits"), params)
        return page_result(items, page, per_page)
