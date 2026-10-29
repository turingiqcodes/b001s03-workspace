"""Configuration, startup validation, PAT precedence, and GitHub App JWT/token caching."""

import asyncio
from datetime import datetime, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from github_mcp.auth import GitHubAppTokenProvider, StaticTokenProvider, build_token_provider
from github_mcp.config import ConfigError, Settings
from github_mcp.errors import AuthenticationError
from github_mcp.server import build_server
from mcp import Client

from .conftest import MockGitHub

APP_ENV = {"GITHUB_APP_ID": "12345", "GITHUB_APP_INSTALLATION_ID": "678"}


def settings(env):
    return Settings.from_env(env)


def test_defaults():
    s = settings({"GITHUB_TOKEN": "t"})
    assert (s.timeout, s.max_retries, s.max_text_chars, s.max_rate_limit_wait) == (30.0, 3, 20_000, 60.0)
    assert s.api_url == "https://api.github.com" and s.transport == "stdio" and s.port == 8000
    assert s.dry_run is False


def test_custom_values_and_trailing_slash():
    s = settings({"GITHUB_TOKEN": "t", "GITHUB_API_URL": "https://ghe.example.com/api/v3/", "GITHUB_TIMEOUT": "5",
                  "GITHUB_MAX_RETRIES": "0", "GITHUB_MAX_TEXT_CHARS": "500", "GITHUB_DRY_RUN": "true",
                  "MCP_TRANSPORT": "http", "MCP_HOST": "0.0.0.0", "MCP_PORT": "9000"})
    assert s.api_url == "https://ghe.example.com/api/v3"
    assert (s.timeout, s.max_retries, s.max_text_chars, s.dry_run) == (5.0, 0, 500, True)
    assert (s.transport, s.host, s.port) == ("http", "0.0.0.0", 9000)


@pytest.mark.parametrize("env,match", [
    ({"GITHUB_MAX_RETRIES": "-1"}, "GITHUB_MAX_RETRIES"),
    ({"GITHUB_TIMEOUT": "abc"}, "GITHUB_TIMEOUT"),
    ({"MCP_PORT": "70000"}, "MCP_PORT"),
    ({"GITHUB_DRY_RUN": "maybe"}, "GITHUB_DRY_RUN"),
    ({"MCP_TRANSPORT": "sse"}, "MCP_TRANSPORT"),
    ({"GITHUB_API_URL": "ftp://x"}, "GITHUB_API_URL"),
])
def test_invalid_values_rejected(env, match):
    with pytest.raises(ConfigError, match=match):
        settings({"GITHUB_TOKEN": "t", **env})


def test_missing_credentials_fail_startup_with_clear_error():
    with pytest.raises(ConfigError, match="No GitHub credentials configured.*GITHUB_TOKEN"):
        settings({}).validate_startup()
    with pytest.raises(ConfigError, match="GITHUB_PER_REQUEST_AUTH"):
        settings({"MCP_TRANSPORT": "http"}).validate_startup()


def test_per_request_mode_allows_startup_without_credentials_only_in_http():
    settings({"MCP_TRANSPORT": "http", "GITHUB_PER_REQUEST_AUTH": "true"}).validate_startup()
    with pytest.raises(ConfigError):
        settings({"MCP_TRANSPORT": "stdio", "GITHUB_PER_REQUEST_AUTH": "true"}).validate_startup()


def test_incomplete_app_config_rejected():
    with pytest.raises(ConfigError, match="GITHUB_APP_INSTALLATION_ID"):
        settings({"GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "x"})


def test_invalid_private_key_rejected_without_echoing_it():
    with pytest.raises(ConfigError) as exc:
        settings({**APP_ENV, "GITHUB_APP_PRIVATE_KEY": "super-secret-not-a-key"})
    assert "super-secret" not in str(exc.value)


def test_app_key_inline_with_escaped_newlines(rsa_private_key_pem):
    s = settings({**APP_ENV, "GITHUB_APP_PRIVATE_KEY": rsa_private_key_pem.replace("\n", "\\n")})
    assert s.auth_method == "github_app" and s.app.private_key == rsa_private_key_pem


def test_app_key_from_file(tmp_path, rsa_private_key_pem):
    key_file = tmp_path / "app.pem"
    key_file.write_text(rsa_private_key_pem)
    s = settings({**APP_ENV, "GITHUB_APP_PRIVATE_KEY_PATH": str(key_file)})
    assert s.auth_method == "github_app"
    with pytest.raises(ConfigError, match="could not be read"):
        settings({**APP_ENV, "GITHUB_APP_PRIVATE_KEY_PATH": str(tmp_path / "missing.pem")})


def test_pat_takes_precedence_over_app(rsa_private_key_pem):
    s = settings({"GITHUB_TOKEN": "pat", **APP_ENV, "GITHUB_APP_PRIVATE_KEY": rsa_private_key_pem})
    assert s.auth_method == "pat"
    provider = build_token_provider(s, httpx.AsyncClient())
    assert isinstance(provider, StaticTokenProvider)


def test_secrets_not_in_repr(rsa_private_key_pem):
    s = settings({"GITHUB_TOKEN": "ghp_SECRET", **APP_ENV})
    assert "ghp_SECRET" not in repr(s)
    assert "SECRET" not in repr(StaticTokenProvider("ghp_SECRET"))


def test_dotenv_file_is_loaded(tmp_path, monkeypatch):
    for var in ("GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_MAX_RETRIES"):
        monkeypatch.delenv(var, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=from-dotenv\nGITHUB_MAX_RETRIES=5\n")
    s = Settings.from_env(dotenv_path=env_file)
    assert s.token == "from-dotenv" and s.max_retries == 5
    # load_dotenv mutates os.environ; monkeypatch restores the deleted vars only, so clean up explicitly.
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.delenv("GITHUB_MAX_RETRIES")


# --------------------------------------------------------------------------- GitHub App provider
class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _token_handler(issued: list, expires_in: int = 3600):
    def handler(request: httpx.Request) -> httpx.Response:
        issued.append(request)
        n = len(issued)
        expires = datetime.fromtimestamp(handler.clock() + expires_in, tz=timezone.utc)
        return httpx.Response(201, json={"token": f"ghs_install_{n}", "expires_at": expires.isoformat().replace("+00:00", "Z")})
    return handler


def _provider(pem, clock, issued, expires_in=3600, api_url="https://api.github.com"):
    handler = _token_handler(issued, expires_in)
    handler.clock = clock
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubAppTokenProvider("12345", "678", pem, api_url, http, clock=clock)


def test_jwt_claims_have_nine_minute_lifetime(rsa_private_key_pem):
    clock = Clock(1_700_000_000)
    provider = _provider(rsa_private_key_pem, clock, [])
    public_key = load_pem_private_key(rsa_private_key_pem.encode(), None).public_key()
    claims = jwt.decode(provider.create_jwt(), public_key, algorithms=["RS256"],
                        options={"verify_exp": False, "verify_iat": False})
    assert claims["iss"] == "12345"
    assert claims["exp"] - clock.now == 540  # nine minutes
    assert clock.now - claims["iat"] == 60  # backdated for clock skew


async def test_installation_token_cached_until_60s_before_expiry(rsa_private_key_pem):
    clock = Clock(1_700_000_000)
    issued: list = []
    provider = _provider(rsa_private_key_pem, clock, issued)
    assert await provider.get_token() == "ghs_install_1"
    assert issued[0].url.path == "/app/installations/678/access_tokens"
    assert issued[0].headers["Authorization"].startswith("Bearer ey")

    clock.now += 3600 - 61  # 61s before expiry: still cached
    assert await provider.get_token() == "ghs_install_1"
    assert len(issued) == 1

    clock.now += 2  # 59s before expiry: refresh
    assert await provider.get_token() == "ghs_install_2"
    assert len(issued) == 2


async def test_concurrent_requests_share_one_token_exchange(rsa_private_key_pem):
    issued: list = []
    provider = _provider(rsa_private_key_pem, Clock(1_700_000_000), issued)
    tokens = await asyncio.gather(*(provider.get_token() for _ in range(10)))
    assert set(tokens) == {"ghs_install_1"} and len(issued) == 1


async def test_installation_token_failure_is_readable(rsa_private_key_pem):
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"message": "A JSON web token could not be decoded"})))
    provider = GitHubAppTokenProvider("1", "2", rsa_private_key_pem, "https://api.github.com", http)
    with pytest.raises(AuthenticationError, match="HTTP 401.*could not be decoded"):
        await provider.get_token()


async def test_app_token_used_by_tools_end_to_end(rsa_private_key_pem):
    """The shared client authenticates tool calls with the installation token, and caches it."""

    gh = MockGitHub()
    gh.add("POST", "/app/installations/678/access_tokens",
           httpx.Response(201, json={"token": "ghs_installation", "expires_at": "2999-01-01T00:00:00Z"}))
    gh.add("GET", "/repos/o/r", {"name": "r"})
    s = settings({**APP_ENV, "GITHUB_APP_PRIVATE_KEY": rsa_private_key_pem})
    mcp, state = build_server(s, transport=gh.transport)
    async with Client(mcp) as client:
        for _ in range(2):
            result = await client.call_tool("get_repository", {"owner": "o", "repo": "r"})
            assert not result.is_error
    await state.aclose()
    repo_calls = [r for r in gh.requests if r.url.path == "/repos/o/r"]
    assert all(r.headers["Authorization"] == "Bearer ghs_installation" for r in repo_calls)
    assert sum(r.url.path.endswith("access_tokens") for r in gh.requests) == 1
