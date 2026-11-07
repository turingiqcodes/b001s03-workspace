"""Shared HTTP plumbing for the real integration clients (NFR-001/010/017).

* :func:`send` performs one logical request, retrying *transient* failures (connection
  errors, timeouts, HTTP 429 and 5xx) a bounded number of times with backoff. Writes
  are never retried here unless the caller says the call is idempotent, so a timed-out
  write is reported, not repeated.
* HTTP errors are mapped to the integration's exception class with a ``retriable`` flag.
* :func:`parse` validates a response payload with a pydantic model and raises
  :class:`MalformedResponseError` instead of passing bad data on (NFR-010).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..exceptions import IntegrationError, MalformedResponseError

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _retry_after_seconds(response: httpx.Response, default: float) -> float:
    value = response.headers.get("Retry-After")
    try:
        return min(float(value), 30.0) if value else default
    except ValueError:
        return default


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    error_cls: type[IntegrationError],
    retries: int = 2,
    idempotent: bool | None = None,
    backoff: float = 0.5,
    sleep: Callable[[float], None] | None = None,
    is_transient: Callable[[httpx.Response], bool] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request and return a 2xx response, or raise ``error_cls``."""
    method = method.upper()
    sleep = sleep or time.sleep  # resolved per call so tests can patch time.sleep
    can_retry = idempotent if idempotent is not None else method in READ_METHODS
    attempts = (retries + 1) if can_retry else 1
    last_error: IntegrationError | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            last_error = error_cls(f"{method} {url}: timed out", retriable=True)
            last_error.__cause__ = exc
            delay = backoff * attempt
        except httpx.TransportError as exc:
            last_error = error_cls(f"{method} {url}: connection failed ({type(exc).__name__})", retriable=True)
            last_error.__cause__ = exc
            delay = backoff * attempt
        else:
            transient = response.status_code in TRANSIENT_STATUS or bool(is_transient and is_transient(response))
            if response.is_success:
                return response
            last_error = error_cls(
                f"{method} {url}: HTTP {response.status_code} {_error_detail(response)}",
                retriable=transient,
                status_code=response.status_code,
            )
            if not transient:
                raise last_error
            delay = _retry_after_seconds(response, backoff * attempt)
        if attempt < attempts:
            log.warning("%s (attempt %d/%d), retrying in %.1fs", last_error, attempt, attempts, delay)
            sleep(delay)

    assert last_error is not None
    raise last_error


def _error_detail(response: httpx.Response) -> str:
    """A short, secret-free description of an error response."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or ""
    if isinstance(body, dict):
        for key in ("detail", "message", "title", "error"):
            if isinstance(body.get(key), str):
                return body[key][:300]
        errors = body.get("errors")  # GraphQL
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message", ""))[:300]
    return response.reason_phrase or ""


def json_body(response: httpx.Response, service: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise MalformedResponseError(service, "response is not JSON") from exc


def parse(model: type[M], data: Any, service: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise MalformedResponseError(service, f"{model.__name__}.{where}: {first['msg']}") from None


def parse_list(model: type[M], items: Any, service: str, what: str) -> list[M]:
    if not isinstance(items, list):
        raise MalformedResponseError(service, f"expected a list of {what}")
    return [parse(model, item, service) for item in items]
