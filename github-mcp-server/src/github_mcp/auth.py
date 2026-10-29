"""Token providers for PAT and GitHub App authentication."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Callable, Protocol

import httpx
import jwt

from .config import Settings
from .errors import AuthenticationError

JWT_LIFETIME_SECONDS = 9 * 60
JWT_CLOCK_SKEW_SECONDS = 60
TOKEN_REFRESH_MARGIN_SECONDS = 60


class TokenProvider(Protocol):
    async def get_token(self) -> str: ...


class StaticTokenProvider:
    """A fixed token: a PAT from configuration or a per-request X-GitHub-Token."""

    def __init__(self, token: str):
        if not token:
            raise AuthenticationError("Empty GitHub token")
        self._token = token

    async def get_token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "StaticTokenProvider(token=***)"


class GitHubAppTokenProvider:
    """Exchanges a short-lived app JWT for an installation token and caches it.

    The cached token is reused until 60 seconds before it expires.
    """

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
        api_url: str,
        http_client: httpx.AsyncClient,
        clock: Callable[[], float] = time.time,
    ):
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key = private_key
        self._api_url = api_url.rstrip("/")
        self._http = http_client
        self._clock = clock
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"GitHubAppTokenProvider(app_id={self._app_id!r}, installation_id={self._installation_id!r})"

    def create_jwt(self) -> str:
        now = int(self._clock())
        payload = {
            "iat": now - JWT_CLOCK_SKEW_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            "iss": str(self._app_id),
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def _cached_token_valid(self) -> bool:
        return self._token is not None and self._clock() < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS

    async def get_token(self) -> str:
        if self._cached_token_valid():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._cached_token_valid():
                return self._token  # type: ignore[return-value]
            await self._refresh()
            return self._token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        url = f"{self._api_url}/app/installations/{self._installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {self.create_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = await self._http.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthenticationError(
                f"Could not reach GitHub to create an installation token ({exc.__class__.__name__})"
            ) from None
        if response.status_code != 201:
            try:
                detail = response.json().get("message", "")
            except ValueError:
                detail = ""
            raise AuthenticationError(
                f"GitHub App installation token request failed with HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
                + ". Check GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID and the private key."
            )
        data = response.json()
        self._token = data["token"]
        expires_at = data.get("expires_at")
        if expires_at:
            self._expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
        else:  # GitHub documents a one-hour lifetime.
            self._expires_at = self._clock() + 3600


def build_token_provider(settings: Settings, http_client: httpx.AsyncClient) -> TokenProvider | None:
    """Return the provider for the server's own credentials (PAT wins), or None if unconfigured."""
    if settings.token:
        return StaticTokenProvider(settings.token)
    if settings.app:
        return GitHubAppTokenProvider(
            app_id=settings.app.app_id,
            installation_id=settings.app.installation_id,
            private_key=settings.app.private_key,
            api_url=settings.api_url,
            http_client=http_client,
        )
    return None
