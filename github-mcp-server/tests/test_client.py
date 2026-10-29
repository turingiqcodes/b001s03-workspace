"""Retry, rate-limit, error-mapping, and base-URL behaviour of the GitHub client."""

import httpx
import pytest

from github_mcp.auth import StaticTokenProvider
from github_mcp.client import GitHubClient, new_http_client
from github_mcp.errors import GitHubAPIError, NetworkError, NotFoundError, RateLimitError

from .conftest import TEST_TOKEN, MockGitHub, call, error_text, make_settings

NOW = 1_700_000_000.0


def make_client(gh: MockGitHub, sleep, **overrides) -> GitHubClient:
    s = make_settings(**overrides)
    return GitHubClient(s, StaticTokenProvider(TEST_TOKEN), new_http_client(s, gh.transport),
                        sleep=sleep, clock=lambda: NOW)


def err(status, message="boom", headers=None):
    return httpx.Response(status, json={"message": message}, headers=headers or {})


# ------------------------------------------------------------------ reads retry transient failures
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_read_retries_on_transient_5xx(gh, no_sleep, status):
    gh.add("GET", "/repos/o/r", err(status), {"ok": True})
    client = make_client(gh, no_sleep)
    assert await client.get_json("repos/o/r") == {"ok": True}
    assert len(gh.requests) == 2 and len(no_sleep.calls) == 1


async def test_read_retries_on_network_error_then_succeeds(gh, no_sleep):
    gh.add("GET", "/repos/o/r", httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), {"ok": True})
    assert await make_client(gh, no_sleep).get_json("repos/o/r") == {"ok": True}
    assert len(gh.requests) == 3


async def test_read_gives_up_after_max_retries(gh, no_sleep):
    gh.add("GET", "/repos/o/r", err(503))
    with pytest.raises(GitHubAPIError, match="503"):
        await make_client(gh, no_sleep, max_retries=2).get_json("repos/o/r")
    assert len(gh.requests) == 3  # 1 + 2 retries


async def test_backoff_grows_exponentially_with_jitter(gh, no_sleep):
    gh.add("GET", "/repos/o/r", err(502))
    with pytest.raises(GitHubAPIError):
        await make_client(gh, no_sleep, max_retries=3).get_json("repos/o/r")
    d0, d1, d2 = no_sleep.calls
    assert 1 <= d0 <= 1.5 and 2 <= d1 <= 3 and 4 <= d2 <= 6


async def test_zero_retries_disables_retry(gh, no_sleep):
    gh.add("GET", "/repos/o/r", err(503), {"ok": True})
    with pytest.raises(GitHubAPIError):
        await make_client(gh, no_sleep, max_retries=0).get_json("repos/o/r")
    assert len(gh.requests) == 1 and no_sleep.calls == []


async def test_non_retryable_4xx_not_retried(gh, no_sleep):
    gh.add("GET", "/repos/o/r", err(422, "Validation Failed"))
    with pytest.raises(GitHubAPIError, match="422.*Validation Failed"):
        await make_client(gh, no_sleep).get_json("repos/o/r")
    assert len(gh.requests) == 1


# ------------------------------------------------------------------ writes never retry 5xx/network
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_write_not_retried_after_5xx(gh, no_sleep, method):
    gh.add(method, "/repos/o/r/issues", err(502), {"ok": True})
    with pytest.raises(GitHubAPIError, match="NOT retried"):
        await make_client(gh, no_sleep).write_json(method, "repos/o/r/issues", {"title": "x"})
    assert len(gh.requests) == 1 and no_sleep.calls == []


async def test_write_not_retried_after_network_error(gh, no_sleep):
    gh.add("POST", "/repos/o/r/issues", httpx.ReadTimeout("timeout"), {"ok": True})
    with pytest.raises(NetworkError, match="may already have been applied"):
        await make_client(gh, no_sleep).write_json("POST", "repos/o/r/issues", {"title": "x"})
    assert len(gh.requests) == 1


async def test_write_retried_on_rate_limit(gh, no_sleep):
    gh.add("POST", "/repos/o/r/issues", err(429, headers={"Retry-After": "2"}), {"number": 1})
    assert (await make_client(gh, no_sleep).write_json("POST", "repos/o/r/issues", {}))["number"] == 1
    assert no_sleep.calls == [2.0] and len(gh.requests) == 2


# ------------------------------------------------------------------ rate limits
async def test_retry_after_header_respected(gh, no_sleep):
    gh.add("GET", "/x", err(429, headers={"Retry-After": "7"}), {"ok": 1})
    await make_client(gh, no_sleep).get_json("x")
    assert no_sleep.calls == [7.0]


async def test_primary_rate_limit_403_waits_until_reset(gh, no_sleep):
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 10))}
    gh.add("GET", "/x", err(403, "API rate limit exceeded", headers), {"ok": 1})
    await make_client(gh, no_sleep).get_json("x")
    assert no_sleep.calls == [11.0]  # reset delta + 1s safety margin


async def test_secondary_rate_limit_403_uses_backoff(gh, no_sleep):
    gh.add("GET", "/x", err(403, "You have exceeded a secondary rate limit"), {"ok": 1})
    await make_client(gh, no_sleep).get_json("x")
    assert len(no_sleep.calls) == 1 and 2 <= no_sleep.calls[0] <= 3


async def test_permission_403_is_not_treated_as_rate_limit(gh, no_sleep):
    gh.add("GET", "/x", err(403, "Resource not accessible by integration"))
    with pytest.raises(GitHubAPIError, match="403.*not accessible") as exc:
        await make_client(gh, no_sleep).get_json("x")
    assert not isinstance(exc.value, RateLimitError)
    assert len(gh.requests) == 1


async def test_wait_above_cap_fails_immediately_with_reset_time(gh, no_sleep):
    reset = int(NOW + 3600)
    gh.add("GET", "/x", err(403, "API rate limit exceeded",
                            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}))
    with pytest.raises(RateLimitError) as exc:
        await make_client(gh, no_sleep, max_rate_limit_wait=60).get_json("x")
    assert exc.value.reset_at == "2023-11-14T23:13:20+00:00"
    assert "reset at 2023-11-14T23:13:20+00:00" in str(exc.value)
    assert no_sleep.calls == [] and len(gh.requests) == 1


async def test_retry_after_above_cap_fails_immediately(gh, no_sleep):
    gh.add("GET", "/x", err(429, headers={"Retry-After": "120"}))
    with pytest.raises(RateLimitError, match="more than the configured maximum of 60s"):
        await make_client(gh, no_sleep).get_json("x")
    assert no_sleep.calls == []


async def test_rate_limit_retries_exhausted(gh, no_sleep):
    gh.add("GET", "/x", err(429, headers={"Retry-After": "1"}))
    with pytest.raises(RateLimitError, match="retries exhausted"):
        await make_client(gh, no_sleep, max_retries=2).get_json("x")
    assert len(gh.requests) == 3


# ------------------------------------------------------------------ error mapping
async def test_404_maps_to_readable_not_found(gh, no_sleep):
    with pytest.raises(NotFoundError, match=r"Not found: GET /repos/o/missing returned 404"):
        await make_client(gh, no_sleep).get_json("repos/o/missing")


async def test_401_maps_to_bad_credentials_without_leaking_token(gh, no_sleep):
    gh.add("GET", "/x", err(401, "Bad credentials"))
    with pytest.raises(GitHubAPIError) as exc:
        await make_client(gh, no_sleep).get_json("x")
    assert "Bad credentials" in str(exc.value) and TEST_TOKEN not in str(exc.value)


async def test_422_error_details_included(gh, no_sleep):
    gh.add("POST", "/repos/o/r/pulls", httpx.Response(422, json={
        "message": "Validation Failed", "errors": [{"message": "A pull request already exists"}]}))
    with pytest.raises(GitHubAPIError, match="Validation Failed \\(A pull request already exists\\)"):
        await make_client(gh, no_sleep).write_json("POST", "repos/o/r/pulls", {})


async def test_tool_errors_are_mcp_errors_and_server_keeps_running(gh):
    gh.add("GET", "/repos/o/r", {"full_name": "o/r"})
    from .conftest import connect

    async with connect(gh) as client:
        bad = await client.call_tool("get_repository", {"owner": "o", "repo": "missing"})
        assert "Not found" in error_text(bad)
        bad2 = await client.call_tool("get_repository", {"owner": "bad/owner", "repo": "r"})
        assert bad2.is_error
        good = await client.call_tool("get_repository", {"owner": "o", "repo": "r"})
        assert not good.is_error and good.structured_content["full_name"] == "o/r"


async def test_tool_rate_limit_error_message_contains_reset(gh):
    gh.add("GET", "/repos/o/r", err(429, headers={"Retry-After": "999"}))
    result = await call(gh, "get_repository", {"owner": "o", "repo": "r"})
    assert "Rate limited; reset at" in error_text(result)


# ------------------------------------------------------------------ headers and base URL
async def test_standard_headers_sent(gh, no_sleep):
    gh.add("GET", "/x", {})
    await make_client(gh, no_sleep).get_json("x")
    req = gh.requests[0]
    assert req.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert req.headers["Accept"] == "application/vnd.github+json"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"


async def test_custom_enterprise_base_url():
    gh = MockGitHub()
    gh.add("GET", "/api/v3/repos/o/r", {"full_name": "o/r"})
    result = await call(gh, "get_repository", {"owner": "o", "repo": "r"},
                        api_url="https://ghe.example.com/api/v3")
    assert not result.is_error
    assert str(gh.requests[0].url) == "https://ghe.example.com/api/v3/repos/o/r"


async def test_timeout_setting_applied():
    s = make_settings(timeout=7.5)
    http = new_http_client(s)
    assert http.timeout.read == 7.5
    await http.aclose()
