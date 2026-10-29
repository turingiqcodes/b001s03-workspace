"""Command-line entry point: ``python -m github_mcp`` or ``github-mcp-server``."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys

from .config import ConfigError, Settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="github-mcp-server", description="GitHub MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], help="Overrides MCP_TRANSPORT (default stdio).")
    parser.add_argument("--host", help="HTTP bind host. Overrides MCP_HOST (default 127.0.0.1).")
    parser.add_argument("--port", type=int, help="HTTP port. Overrides MCP_PORT (default 8000).")
    parser.add_argument("--env-file", help="Path to a .env file (default: ./.env if present).")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings.from_env(dotenv_path=args.env_file)
        overrides = {k: v for k, v in {"transport": args.transport, "host": args.host, "port": args.port}.items()
                     if v is not None}
        settings = dataclasses.replace(settings, **overrides)
        settings.validate_startup()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    # Logs go to stderr: stdout carries the MCP protocol in stdio mode.
    logging.basicConfig(
        level=settings.log_level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx logs full request URLs at INFO; keep them quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.check_config:
        print(f"Configuration OK (transport={settings.transport}, auth={settings.auth_method}, "
              f"api_url={settings.api_url}, dry_run={settings.dry_run})", file=sys.stderr)
        return 0

    from .server import run_http, run_stdio

    try:
        asyncio.run(run_http(settings) if settings.transport == "http" else run_stdio(settings))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
