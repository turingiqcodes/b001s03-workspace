"""Shared tool-registration helper."""

import functools
import logging
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..errors import GitHubError

logger = logging.getLogger("github_mcp.tools")


def github_tool(
    mcp: MCPServer,
    description: str,
    *,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register an async function as an MCP tool named after the function.

    GitHub and validation errors become MCP tool errors (``isError: true``) with a
    readable message, so one failing call never takes the server down.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except GitHubError as exc:
                logger.info("Tool %s failed: %s", fn.__name__, exc)
                raise ToolError(str(exc)) from None
            except ValueError as exc:
                raise ToolError(f"Invalid input: {exc}") from None

        mcp.tool(
            name=fn.__name__,
            description=description,
            annotations=ToolAnnotations(
                readOnlyHint=read_only,
                destructiveHint=destructive if not read_only else None,
                idempotentHint=idempotent if idempotent is not None else read_only,
                openWorldHint=True,
            ),
            structured_output=True,
        )(wrapper)
        return fn

    return decorator
