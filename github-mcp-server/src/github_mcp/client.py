"""Asynchronous GitHub REST client with retry, rate-limit, and error-mapping logic.

Retry policy:
* Reads (GET/HEAD) retry on 500/502/503/504 and network errors.
* Writes never retry after a 5xx or network error: the change may already have happened.
* Reads and writes retry on rate limits (429, or 403 identified as a rate limit),
  honouring Retry-After / X-RateLimit-Reset. If the required wait exceeds the
  configured cap the call fails immediately with the reset time.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from . import __version__
from .auth import TokenProvider
from .config import Settings
from .errors import AuthenticationError, GitHubAPIError, NetworkError, NotFoundError, RateLimitError

logger = logging.getLogger(__name__)

READ_METHODS = frozenset({"GET", "HEAD"})
RETRYABLE_READ_STATUSES = frozenset({500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 1.0
API_VERSION = "2022-11-28"


def new_http_client(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.api_url + "/",
        timeout=settings.timeout,
        transport=transport,
        follow_redirects=True,  # job logs redirect to blob storage; httpx drops auth cross-origin
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"github-mcp-server/{__version__}",
        },
    )


def _format_reset(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:500] or response.reason_phrase
    if not isinstance(data, dict):
        return response.reason_phrase
    message = str(data.get("message") or response.reason_phrase)
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        details = []
        for err in errors[:5]:
            if isinstance(err, dict):
                details.append(err.get("message") or " ".join(f"{k}={v}" for k, v in err.items()))
            else:
                details.append(str(err))
        message += " (" + "; ".join(details) + ")"
    return message


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider | None,
        http_client: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self._tokens = token_provider
        self._http = http_client
        self._sleep = sleep
        self._clock = clock

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ helpers
    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.request("GET", path, params=params)).json()

    async def get_text(self, path: str, *, accept: str, params: dict[str, Any] | None = None) -> str:
        return (await self.request("GET", path, params=params, accept=accept)).text

    async def write_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        response = await self.request(method, path, json=body)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    # ------------------------------------------------------------------ core
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        accept: str | None = None,
    ) -> httpx.Response:
        method = method.upper()
        path = path.lstrip("/")
        display = "/" + path
        is_read = method in READ_METHODS
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        if self._tokens is None:
            raise AuthenticationError(
                "No GitHub credentials available for this request. Configure GITHUB_TOKEN or a "
                "GitHub App, or send an X-GitHub-Token header (HTTP mode)."
            )
        headers = {"Authorization": f"Bearer {await self._tokens.get_token()}"}
        if accept:
            headers["Accept"] = accept

        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:
                kind = exc.__class__.__name__
                if is_read and attempt < self.settings.max_retries:
                    delay = self._backoff(attempt)
                    logger.warning("%s %s network error (%s); retrying in %.1fs", method, display, kind, delay)
                    attempt += 1
                    await self._sleep(delay)
                    continue
                if is_read:
                    raise NetworkError(f"Network error for {method} {display}: {kind}") from None
                raise NetworkError(
                    f"Network error for {method} {display}: {kind}. The write was NOT retried because it "
                    "may already have been applied; verify the current state before trying again."
                ) from None

            logger.debug("%s %s -> %s", method, display, response.status_code)

            if self._is_rate_limited(response):
                wait, reset_at = self._rate_limit_wait(response, attempt)
                if wait > self.settings.max_rate_limit_wait:
                    raise RateLimitError(
                        method,
                        display,
                        f"Rate limited; reset at {reset_at} (requires waiting {wait:.0f}s, more than the "
                        f"configured maximum of {self.settings.max_rate_limit_wait:.0f}s)",
                        reset_at,
                    )
                if attempt >= self.settings.max_retries:
                    raise RateLimitError(
                        method, display, f"Rate limited and retries exhausted; reset at {reset_at}", reset_at
                    )
                logger.warning("%s %s rate limited; waiting %.1fs", method, display, wait)
                attempt += 1
                await self._sleep(wait)
                continue

            if (
                is_read
                and response.status_code in RETRYABLE_READ_STATUSES
                and attempt < self.settings.max_retries
            ):
                delay = self._backoff(attempt)
                logger.warning("%s %s -> %s; retrying in %.1fs", method, display, response.status_code, delay)
                attempt += 1
                await self._sleep(delay)
                continue

            if response.status_code >= 400:
                self._raise_for_status(response, method, display, is_read)
            return response

    def _backoff(self, attempt: int) -> float:
        delay = BACKOFF_BASE_SECONDS * (2**attempt)
        delay += random.uniform(0, delay / 2)  # jitter
        return min(delay, self.settings.max_rate_limit_wait)

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False
        if response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers:
            return True
        try:
            message = str(response.json().get("message", ""))
        except (ValueError, AttributeError):
            return False
        return "rate limit" in message.lower()

    def _rate_limit_wait(self, response: httpx.Response, attempt: int) -> tuple[float, str]:
        now = self._clock()
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                wait = max(0.0, float(retry_after))
                return wait, _format_reset(now + wait)
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if response.headers.get("x-ratelimit-remaining") == "0" and reset is not None:
            try:
                reset_epoch = float(reset)
                return max(0.0, reset_epoch - now) + 1.0, _format_reset(reset_epoch)
            except ValueError:
                pass
        # Secondary rate limit without guidance: exponential backoff with jitter.
        delay = BACKOFF_BASE_SECONDS * (2**attempt) * 2
        delay += random.uniform(0, delay / 2)
        return delay, _format_reset(now + delay)

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str, path: str, is_read: bool) -> None:
        status = response.status_code
        message = _error_message(response)
        if status == 404:
            raise NotFoundError(method, path, message)
        if status == 401:
            raise GitHubAPIError(status, f"Bad credentials or expired token ({message})", method, path)
        if status >= 500 and not is_read:
            message += (
                ". The write was NOT retried because it may already have been applied; "
                "verify the current state before trying again"
            )
        raise GitHubAPIError(status, message, method, path)
