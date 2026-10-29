# GitHub MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets AI agents and MCP clients work with GitHub through
28 structured tools backed by the GitHub REST API. Built on the official Python MCP SDK (v2, `MCPServer`), `httpx`,
and Pydantic. Supports stdio and streamable HTTP (`/mcp`), PAT and GitHub App auth, per-request tokens, dry-run mode,
retries and rate-limit handling, and GitHub Enterprise.

## Project layout

```
src/github_mcp/
  config.py        Settings from env/.env, validation, startup checks
  auth.py          PAT + GitHub App (JWT, cached installation tokens)
  client.py        Async GitHub client: retries, rate limits, error mapping
  errors.py        Readable exception hierarchy
  schemas.py       Pydantic-validated input types, pagination, truncation helpers
  state.py         Shared vs per-request clients, dry-run preview helpers
  server.py        MCPServer assembly, /health, stdio and HTTP runners
  __main__.py      CLI entry point (github-mcp-server / python -m github_mcp)
  tools/           repos.py, pulls.py, actions.py, issues.py, search.py (+ common.py, inventory in __init__.py)
tests/             pytest suite using httpx.MockTransport (no network, no real credentials)
```

## Tools (28)

| Group | Tools |
|---|---|
| Repository (5) | `get_repository`, `list_repository_branches`, `list_repository_files`, `get_file_contents`, `get_repository_commits` |
| Pull requests (11) | `get_pull_request`, `get_pull_request_diff`, `list_pull_request_files`, `list_pull_request_comments`, `list_pull_request_reviews`, `create_pull_request_comment`, `create_pull_request_review`, `submit_pull_request_review`, `create_pull_request`, `update_pull_request`, `merge_pull_request` |
| CI/CD (4) | `list_workflow_runs`, `get_workflow_run`, `list_workflow_run_jobs`, `get_workflow_job_logs` |
| Issues (6) | `get_issue`, `list_issues`, `create_issue`, `add_labels`, `update_issue`, `close_issue` |
| Search (2) | `search_issues`, `search_code` |

**About the tool count in the spec:** The spec's headings say "Pull request tools — 10" and "Issue tools — 7", but
those sections list 11 and 6 names. The two errors cancel out, so the numbered list holds **28** unique names, which
matches "exactly 28". (The note claiming 29 names was itself a miscount.) Every name is implemented exactly as written,
and `tests/test_registry.py` asserts the exact set.

**Write tools (10):** `create_pull_request_comment`, `create_pull_request_review`, `submit_pull_request_review`,
`create_pull_request`, `update_pull_request`, `merge_pull_request`, `create_issue`, `add_labels`, `update_issue`,
`close_issue`. This matches the spec's count of ten.

### Behaviour notes

- **Pagination:** `page` ≥ 1; `per_page` 1–100 (default 30). List results are wrapped as
  `{items, count, page, per_page, has_more_hint, ...}`.
- **Files:** content is returned only for UTF-8 text ≤ 1 MB. Larger or binary files return `content: null` and a
  `reason`. Passing a directory returns an error that points to `list_repository_files`. Paths containing `.`/`..`
  segments are rejected.
- **Recursive listing:** returns 1,000 entries by default (`max_entries` can go up to 10,000) and sets `truncated`.
- **Diffs** are truncated from the end (the start is kept). **Job logs** are truncated from the start (the end, where
  errors usually are, is kept). The limit is `GITHUB_MAX_TEXT_CHARS`.
- **Two-step reviews:** call `create_pull_request_review` without `event` to get a PENDING `review_id`, then call
  `submit_pull_request_review` with `APPROVE`, `REQUEST_CHANGES` or `COMMENT`.
- **Fork PRs:** set `head` to `fork-owner:branch`.
- **Labels/assignees:** `add_labels` adds to the existing labels. In `update_issue`, `labels`/`assignees`
  **replace** the whole existing set (`[]` clears it; leave the field out to keep it unchanged). The result
  includes `replaced_fields`.
- **`list_issues`** hides pull requests unless `include_pull_requests=true`.
- **`list_pull_request_comments`** with `kind=all|issue|review` returns conversation comments, inline review
  comments, or both.
- **Dry run:** setting `GITHUB_DRY_RUN=true` (or passing `dry_run=true` on a call) makes every write tool return
  `{"status": "dry_run", "dry_run": true, "request": {...}}` and send **no** HTTP request. A real write returns
  `{"status": "completed", "dry_run": false, "result": ...}`. When the server-wide setting is on, `dry_run=false` on a
  call cannot turn it off.

### Reliability

| Situation | Reads (GET) | Writes |
|---|---|---|
| 500/502/503/504 | retried (exponential backoff + jitter) | **not retried**; the error says the write may already have been applied |
| Network error / timeout | retried | **not retried** |
| 429, or a 403 that is a rate limit | retried after `Retry-After` / `X-RateLimit-Reset`, or after backoff for secondary limits | same |
| Required wait > `GITHUB_MAX_RATE_LIMIT_WAIT` | fails immediately, with the reset time | same |
| 404 | readable "Not found" error | same |
| Other 4xx/5xx | readable API error, including GitHub's `errors` details | same |

A 403 caused by permissions (not a rate limit) is never retried. When a tool call fails, the client gets an MCP error
result (`isError: true`) and the server keeps running.

## Installation

Requires Python ≥ 3.10.

Linux/macOS:
```bash
cd github-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
cp .env.example .env    # then edit .env locally; never commit it
```

Windows (VS Code PowerShell):
```powershell
cd github-mcp-server
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt; pip install -e .
Copy-Item .env.example .env   # then edit .env locally
```

## Configuration

Settings are read from environment variables. A `.env` file in the current directory (or the path given with
`--env-file`) is loaded too, but real environment variables take priority.

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | – | Personal access token. Takes precedence over GitHub App settings. |
| `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID` | – | GitHub App auth |
| `GITHUB_APP_PRIVATE_KEY` / `GITHUB_APP_PRIVATE_KEY_PATH` | – | PEM key, inline (`\n` escapes allowed) or as a file |
| `GITHUB_API_URL` | `https://api.github.com` | e.g. `https://ghe.example.com/api/v3` for GitHub Enterprise |
| `GITHUB_TIMEOUT` | `30` | Request timeout in seconds |
| `GITHUB_MAX_RETRIES` | `3` | `0` disables retries |
| `GITHUB_MAX_RATE_LIMIT_WAIT` | `60` | Longest wait in seconds before failing with the reset time |
| `GITHUB_MAX_TEXT_CHARS` | `20000` | Truncation limit for file content, diffs and logs |
| `GITHUB_DRY_RUN` | `false` | Preview every write without sending it |
| `GITHUB_PER_REQUEST_AUTH` | `false` | HTTP only: allow startup with no server credentials |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | HTTP bind address |
| `LOG_LEVEL` | `INFO` | Logs go to stderr. Tokens are never logged. |

**GitHub App:** the server signs a JWT valid for 9 minutes (backdated 60 s to allow for clock skew), exchanges it for
an installation token, and reuses that token until 60 s before it expires.

**Per-request tokens (HTTP mode):** a client can send `X-GitHub-Token: <token>`. That call then uses a separate
client with its own connection pool, which is closed when the call ends, so the token never reaches the shared client
or any other request. With no header, the server's own credentials are used. If the server has none
(`GITHUB_PER_REQUEST_AUTH=true` and no credentials configured), the call fails with an error explaining that the
header is required. The header is ignored in stdio mode.

### Token permissions

A fine-grained personal access token (or a GitHub App) needs these repository permissions. No account or
organization permissions are required.

| Permission | Access | Needed by |
|---|---|---|
| Metadata | Read | Every call (granted automatically) |
| Contents | Read and write | Read: `get_repository`, `list_repository_branches`, `list_repository_files`, `get_file_contents`, `get_repository_commits`, `search_code`. Write: `merge_pull_request` |
| Pull requests | Read and write | Read: `get_pull_request`, `get_pull_request_diff`, `list_pull_request_files`, `list_pull_request_comments`, `list_pull_request_reviews`. Write: `create_pull_request`, `update_pull_request`, `create_pull_request_comment`, `create_pull_request_review`, `submit_pull_request_review` |
| Issues | Read and write | Read: `get_issue`, `list_issues`, `search_issues`. Write: `create_issue`, `update_issue`, `close_issue`, `add_labels` |
| Actions | Read | `list_workflow_runs`, `get_workflow_run`, `list_workflow_run_jobs`, `get_workflow_job_logs` |

Limit the token to the repositories the agent should touch ("Only select repositories"). For read-only use, set
Contents, Pull requests and Issues to Read: the write tools then fail with a permission error, which is not retried.
Leaving Contents at Read blocks only `merge_pull_request`.

The server exits with a clear configuration error (exit code 2) if it has no usable credentials, unless it is in HTTP
per-request mode. Run `github-mcp-server --check-config` to validate settings without starting the server.

## Running

```bash
github-mcp-server                         # stdio (default)
github-mcp-server --transport http --port 8000
curl http://127.0.0.1:8000/health         # {"status":"ok"}
```
`python -m github_mcp ...` works the same way. In HTTP mode the MCP endpoint is `http://HOST:PORT/mcp`.

**Network exposure:** when bound to `127.0.0.1`/`localhost`, the SDK turns on DNS-rebinding protection. Binding to
`0.0.0.0` (the Docker default) turns it off, and the server has **no built-in client authentication**: anyone who can
reach the port can use the server's configured credentials. Only expose it on trusted networks, or put it behind an
authenticating proxy. Running it in per-request mode with no server credentials also limits the risk.

### Docker

```bash
docker compose up --build        # HTTP mode on port 8000; reads .env if present
curl http://localhost:8000/health
```
or
```bash
docker build -t github-mcp-server . && docker run --rm -p 8000:8000 --env-file .env github-mcp-server
```

## Registering with MCP clients

**Claude Code (stdio):**
```bash
claude mcp add github --env GITHUB_TOKEN=<your-token> -- /path/to/github-mcp-server/.venv/bin/github-mcp-server
```
**Claude Code (HTTP):**
```bash
claude mcp add --transport http github http://127.0.0.1:8000/mcp --header "X-GitHub-Token: <your-token>"
```
**Claude Desktop / generic JSON config (stdio):**
```json
{
  "mcpServers": {
    "github": {
      "command": "/path/to/github-mcp-server/.venv/bin/github-mcp-server",
      "env": { "GITHUB_TOKEN": "<your-token>" }
    }
  }
}
```
On Windows, use `C:\\path\\to\\github-mcp-server\\.venv\\Scripts\\github-mcp-server.exe`. Rather than writing the token
into client config files, prefer setting it as an environment variable or in the server's `.env`.

## Testing

```bash
python -m pytest -q
```
The suite needs no credentials and makes no network calls to GitHub. All GitHub traffic goes through
`httpx.MockTransport`. The transport tests start local servers on `127.0.0.1` and stdio subprocesses.

## Requirement → test coverage

| Requirement | Test(s) |
|---|---|
| 28 exact tool names, descriptions, input/output schemas | `test_registry.py` |
| Structured output | `test_registry.py::test_structured_output_returned` |
| stdio transport | `test_transports.py::test_stdio_transport_subprocess` |
| Streamable HTTP `/mcp` | `test_transports.py::test_http_transport_lists_tools_and_serves_health` |
| `/health` without credentials or a session | `test_transports.py::test_health_without_credentials_or_session`, `test_cli_http_server_starts_and_health_responds` |
| Clear startup errors / per-request startup | `test_config_auth.py::test_missing_credentials_*`, `test_per_request_mode_*`, `test_transports.py::test_cli_*` |
| PAT precedence | `test_config_auth.py::test_pat_takes_precedence_over_app` |
| App JWT ~9 min, token cache until 60 s before expiry | `test_config_auth.py::test_jwt_claims_*`, `test_installation_token_cached_*`, `test_concurrent_*`, `test_app_token_used_by_tools_end_to_end` |
| Private key inline/file, invalid key | `test_config_auth.py::test_app_key_*`, `test_invalid_private_key_*` |
| Per-request token isolation and cleanup | `test_transports.py::test_per_request_*`, `test_http_per_request_tokens_never_leak_between_requests`, `test_http_per_request_auth_mode_*` |
| Secrets never in repr, errors or logs | `test_config_auth.py::test_secrets_not_in_repr`, `test_client.py::test_401_*`, `test_transports.py::test_cli_http_server_*` |
| File-content edge cases (1 MB, binary, directory, truncation, traversal) | `test_repos.py::test_get_file_contents_*` |
| Recursive listing (1000 default, 10000 max, flag) | `test_repos.py::test_recursive_*`, `test_max_entries_bounds` |
| PR diff truncation | `test_pulls_actions.py::test_diff_truncation_keeps_head` |
| Job-log truncation (keep tail) | `test_pulls_actions.py::test_job_logs_*` |
| Pending review then submit | `test_pulls_actions.py::test_two_step_pending_review` |
| Fork head, draft PR, merge-method rejection | `test_pulls_actions.py::test_create_draft_pr_from_fork`, `test_merge_methods` |
| Issue validation (empty title/labels), replacement semantics, close reasons, PR hiding | `test_issues_search.py` |
| Search validation | `test_issues_search.py::test_search_rejects_empty_query` |
| Pagination boundaries | `test_issues_search.py::test_pagination_*`, `test_registry.py::test_pagination_bounds_published_in_schema` |
| Dry run for all 10 write tools (zero requests) | `test_dry_run.py` |
| Read retries (5xx, network), backoff, `0` disables | `test_client.py::test_read_*`, `test_backoff_*`, `test_zero_retries_*` |
| No write retries after 5xx/network | `test_client.py::test_write_not_retried_*` |
| Rate-limit retries (Retry-After, reset, secondary), wait cap | `test_client.py::test_retry_after_*`, `test_primary_*`, `test_secondary_*`, `test_wait_above_cap_*`, `test_write_retried_on_rate_limit` |
| 404 and other error mapping; server survives failures | `test_client.py::test_404_*`, `test_422_*`, `test_tool_errors_are_mcp_errors_and_server_keeps_running` |
| Custom API base URL (Enterprise) | `test_client.py::test_custom_enterprise_base_url` |
| In-process end-to-end MCP | every tool test (via `mcp.Client(server)`), `test_registry.py` |

## Known limitations

- Pagination follows GitHub's `page` parameter. `has_more_hint` is a heuristic; `Link` headers are not parsed.
- In `list_issues`, filtering out pull requests can leave a page with fewer than `per_page` items.
- `list_pull_request_comments` with `kind=all` pages both comment types with the same `page`/`per_page`.
- The HTTP transport has no MCP-level client authentication (see Network exposure above).
- Not tested against the live GitHub API. Behaviour is checked against mocked responses that follow GitHub's
  documented formats.
