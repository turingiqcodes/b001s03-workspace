"""Apache Airflow 3.x REST API v2 client (FR-001/002/003/012, FR-021..024, FR-056..059,
NFR-001/010/015).

Authentication (FR-003, documented in .env.example):

* ``AIRFLOW_TOKEN`` — a JWT sent as ``Authorization: Bearer``.
* ``AIRFLOW_USERNAME`` + ``AIRFLOW_PASSWORD`` — exchanged for a JWT at
  ``POST /auth/token``; the token is refreshed before it expires and once on HTTP 401.

Configuration validation rejects any other combination. Every response is validated
with the pydantic models below (extra fields ignored, required fields enforced).

This module only *talks* to Airflow. Whether a clear is allowed is decided by the
policy engine and enforced by ``services/recovery.py`` (dry-run inspection first).
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Annotated, Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..config import AirflowSettings
from ..exceptions import AirflowError, MalformedResponseError
from .http import json_body, parse, parse_list, send

log = logging.getLogger(__name__)

SERVICE = "airflow"
API = "/api/v2"
MAX_PAGES = 1000  # hard stop against a server that keeps returning pages
MAX_LOG_PAGES = 20


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


UTCDateTime = Annotated[datetime, AfterValidator(_utc)]


# --------------------------------------------------------------------------- response models


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class DagInfo(_Model):
    dag_id: str = Field(min_length=1)
    is_paused: bool | None = None
    owners: list[str] = Field(default_factory=list)
    fileloc: str | None = None
    relative_fileloc: str | None = None


class DagRun(_Model):
    dag_id: str = Field(min_length=1)
    dag_run_id: str = Field(min_length=1)
    state: str
    run_type: str | None = None
    logical_date: UTCDateTime | None = None
    start_date: UTCDateTime | None = None
    end_date: UTCDateTime | None = None


class TaskInstance(_Model):
    dag_id: str = Field(min_length=1)
    dag_run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    map_index: int = -1
    state: str | None = None
    try_number: int = Field(0, ge=0)
    operator: str | None = None
    logical_date: UTCDateTime | None = None
    start_date: UTCDateTime | None = None
    end_date: UTCDateTime | None = None


class ImportErrorInfo(_Model):
    import_error_id: int
    filename: str = Field(min_length=1)
    stack_trace: str = ""
    timestamp: UTCDateTime
    bundle_name: str | None = None


# --------------------------------------------------------------------------- interface


@runtime_checkable
class AirflowClient(Protocol):
    """Operations the agent and the Airflow MCP server need. Implemented by
    :class:`HttpAirflowClient` and ``integrations.mocks.MockAirflowClient``."""

    def check_connection(self) -> dict[str, Any]: ...
    def list_dags(self) -> list[DagInfo]: ...
    def get_dag(self, dag_id: str) -> DagInfo: ...
    def list_dag_runs(
        self,
        dag_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        order_by: str = "end_date",
        max_items: int | None = None,
    ) -> list[DagRun]: ...
    def get_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRun]: ...
    def get_dag_run(self, dag_id: str, dag_run_id: str) -> DagRun: ...
    def list_task_instances(
        self,
        dag_id: str = "~",
        dag_run_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        max_items: int | None = None,
    ) -> list[TaskInstance]: ...
    def get_task_instance(self, dag_id: str, dag_run_id: str, task_id: str, map_index: int = -1) -> TaskInstance: ...
    def get_task_log(self, dag_id: str, dag_run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str: ...
    def get_dag_source(self, dag_id: str) -> str: ...
    def list_import_errors(self) -> list[ImportErrorInfo]: ...
    def clear_task_instances(
        self,
        dag_id: str,
        dag_run_id: str,
        task_ids: list[str],
        *,
        dry_run: bool,
        only_failed: bool = True,
        include_downstream: bool = False,
    ) -> list[TaskInstance]: ...
    def set_task_instance_state(
        self, dag_id: str, dag_run_id: str, task_id: str, new_state: str, *, map_index: int = -1, dry_run: bool
    ) -> list[TaskInstance]: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------- authentication


def _jwt_expiry(token: str) -> float | None:
    """Read the ``exp`` claim without verifying the signature (only used to schedule a
    refresh; Airflow verifies the token)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError):
        return None


class _AirflowAuth(httpx.Auth):
    """Bearer auth with token exchange and refresh for username/password logins."""

    REFRESH_MARGIN = 60.0

    def __init__(self, settings: AirflowSettings, token_client: httpx.Client) -> None:
        self._settings = settings
        self._token_client = token_client
        self._lock = threading.Lock()
        self._token: str | None = settings.token.get_secret_value() if settings.token else None
        self._expires: float | None = _jwt_expiry(self._token) if self._token else None
        self._exchange = settings.auth_method == "password"

    def _fetch_token(self) -> None:
        assert self._settings.username and self._settings.password
        response = send(
            self._token_client,
            "POST",
            "/auth/token",
            error_cls=AirflowError,
            retries=self._settings.max_retries_on_transient_error,
            idempotent=True,  # obtaining a token has no side effects
            json={"username": self._settings.username, "password": self._settings.password.get_secret_value()},
        )
        body = json_body(response, SERVICE)
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise MalformedResponseError(SERVICE, "/auth/token did not return access_token")
        self._token, self._expires = token, _jwt_expiry(token)
        log.info("Obtained Airflow access token")

    def _current_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            needs = self._token is None or force_refresh or (
                self._expires is not None and self._expires - time.time() < self.REFRESH_MARGIN
            )
            if needs and self._exchange:
                self._fetch_token()
            if self._token is None:
                raise AirflowError("No Airflow credentials configured")
            return self._token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._current_token()}"
        response = yield request
        if response.status_code == 401 and self._exchange:
            request.headers["Authorization"] = f"Bearer {self._current_token(force_refresh=True)}"
            yield request


# --------------------------------------------------------------------------- client


def _q(value: str) -> str:
    """Quote a path segment (run IDs contain ':' and '+'). '~' (all) is kept."""
    return quote(value, safe="~")


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()  # type: ignore[union-attr]


class HttpAirflowClient:
    """Real Airflow 3 API v2 client. One instance is shared by the agent and MCP server."""

    def __init__(self, settings: AirflowSettings, *, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.base_url:
            raise AirflowError("AIRFLOW_BASE_URL is not configured")
        self._settings = settings
        common: dict[str, Any] = {
            "base_url": settings.base_url,
            "timeout": settings.timeout_seconds,
            "verify": settings.verify_tls,
            "headers": {"Accept": "application/json", "User-Agent": "airflow-oncall-agent/1.0"},
        }
        if transport is not None:
            common["transport"] = transport
        self._token_client = httpx.Client(**common)
        self._client = httpx.Client(auth=_AirflowAuth(settings, self._token_client), **common)

    def close(self) -> None:
        self._client.close()
        self._token_client.close()

    # ----------------------------------------------------------------- plumbing

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = send(
            self._client,
            method,
            path,
            error_cls=AirflowError,
            retries=self._settings.max_retries_on_transient_error,
            **kwargs,
        )
        return json_body(response, SERVICE)

    def _paginate(self, path: str, key: str, params: dict[str, Any], max_items: int | None = None) -> list[Any]:
        """Collect items across limit/offset pages (NFR-015)."""
        items: list[Any] = []
        offset = 0
        page_size = self._settings.page_size
        for _ in range(MAX_PAGES):
            limit = page_size if max_items is None else min(page_size, max_items - len(items))
            body = self._request("GET", path, params={**params, "limit": limit, "offset": offset})
            if not isinstance(body, dict) or not isinstance(body.get(key), list):
                raise MalformedResponseError(SERVICE, f"{path}: missing '{key}' list")
            page = body[key]
            items.extend(page)
            total = body.get("total_entries")
            offset += len(page)
            if not page or len(page) < limit or (isinstance(total, int) and offset >= total):
                break
            if max_items is not None and len(items) >= max_items:
                break
        else:
            log.warning("Stopped paginating %s after %d pages", path, MAX_PAGES)
        return items if max_items is None else items[:max_items]

    # ----------------------------------------------------------------- reads

    def check_connection(self) -> dict[str, Any]:
        """Verify reachability *and* credentials (the version endpoint alone may be public)."""
        version = self._request("GET", f"{API}/version")
        dags = self._request("GET", f"{API}/dags", params={"limit": 1})
        if not isinstance(version, dict) or not isinstance(dags, dict):
            raise MalformedResponseError(SERVICE, "unexpected /version or /dags response")
        return {"version": version.get("version"), "dags_visible": dags.get("total_entries")}

    def list_dags(self) -> list[DagInfo]:
        return parse_list(DagInfo, self._paginate(f"{API}/dags", "dags", {}), SERVICE, "dags")

    def get_dag(self, dag_id: str) -> DagInfo:
        return parse(DagInfo, self._request("GET", f"{API}/dags/{_q(dag_id)}"), SERVICE)

    def list_dag_runs(
        self,
        dag_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        order_by: str = "end_date",
        max_items: int | None = None,
    ) -> list[DagRun]:
        params: dict[str, Any] = {"order_by": order_by}
        if states:
            params["state"] = states
        if end_date_gte is not None:
            params["end_date_gte"] = _iso(end_date_gte)
        raw = self._paginate(f"{API}/dags/{_q(dag_id)}/dagRuns", "dag_runs", params, max_items)
        return parse_list(DagRun, raw, SERVICE, "dag_runs")

    def get_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRun]:
        """Most recent runs first (FR-023)."""
        return self.list_dag_runs(dag_id, order_by="-start_date", max_items=limit)

    def get_dag_run(self, dag_id: str, dag_run_id: str) -> DagRun:
        return parse(DagRun, self._request("GET", f"{API}/dags/{_q(dag_id)}/dagRuns/{_q(dag_run_id)}"), SERVICE)

    def list_task_instances(
        self,
        dag_id: str = "~",
        dag_run_id: str = "~",
        *,
        states: list[str] | None = None,
        end_date_gte: datetime | None = None,
        max_items: int | None = None,
    ) -> list[TaskInstance]:
        params: dict[str, Any] = {"order_by": "end_date"}
        if states:
            params["state"] = states
        if end_date_gte is not None:
            params["end_date_gte"] = _iso(end_date_gte)
        path = f"{API}/dags/{_q(dag_id)}/dagRuns/{_q(dag_run_id)}/taskInstances"
        return parse_list(TaskInstance, self._paginate(path, "task_instances", params, max_items), SERVICE, "task_instances")

    def get_task_instance(self, dag_id: str, dag_run_id: str, task_id: str, map_index: int = -1) -> TaskInstance:
        path = f"{API}/dags/{_q(dag_id)}/dagRuns/{_q(dag_run_id)}/taskInstances/{_q(task_id)}"
        if map_index >= 0:
            path += f"/{map_index}"
        return parse(TaskInstance, self._request("GET", path), SERVICE)

    def get_task_log(self, dag_id: str, dag_run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str:
        """Full log text for one try (FR-021). Structured Airflow 3 log lines are
        flattened to text, including exception type and message."""
        path = f"{API}/dags/{_q(dag_id)}/dagRuns/{_q(dag_run_id)}/taskInstances/{_q(task_id)}/logs/{try_number}"
        params: dict[str, Any] = {"full_content": "true"}
        if map_index >= 0:
            params["map_index"] = map_index
        parts: list[str] = []
        token: str | None = None
        for _ in range(MAX_LOG_PAGES):
            body = self._request("GET", path, params={**params, **({"token": token} if token else {})})
            if not isinstance(body, dict) or "content" not in body:
                raise MalformedResponseError(SERVICE, "task log response has no 'content'")
            text = _flatten_log_content(body["content"])
            if text:
                parts.append(text)
            token = body.get("continuation_token")
            if not token or not text:
                break
        return "\n".join(parts)

    def get_dag_source(self, dag_id: str) -> str:
        """DAG file source as parsed by Airflow (FR-022)."""
        body = self._request("GET", f"{API}/dagSources/{_q(dag_id)}")
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            raise MalformedResponseError(SERVICE, "dagSources response has no 'content'")
        return content

    def list_import_errors(self) -> list[ImportErrorInfo]:
        raw = self._paginate(f"{API}/importErrors", "import_errors", {"order_by": "timestamp"})
        return parse_list(ImportErrorInfo, raw, SERVICE, "import_errors")

    # ----------------------------------------------------------------- writes

    def clear_task_instances(
        self,
        dag_id: str,
        dag_run_id: str,
        task_ids: list[str],
        *,
        dry_run: bool,
        only_failed: bool = True,
        include_downstream: bool = False,
    ) -> list[TaskInstance]:
        """POST clearTaskInstances, scoped to one DAG run. Returns the affected task
        instances (with ``dry_run=True``, the ones that *would* be cleared)."""
        if not task_ids:
            raise AirflowError("clear_task_instances needs at least one task id")
        body = {
            "dry_run": dry_run,
            "dag_run_id": dag_run_id,
            "task_ids": task_ids,
            "only_failed": only_failed,
            "only_running": False,
            "reset_dag_runs": True,
            "include_upstream": False,
            "include_downstream": include_downstream,
            "include_future": False,
            "include_past": False,
        }
        # Never auto-repeat the real clear: a timeout is reported, verification decides.
        data = self._request("POST", f"{API}/dags/{_q(dag_id)}/clearTaskInstances", json=body, idempotent=dry_run)
        if not isinstance(data, dict) or not isinstance(data.get("task_instances"), list):
            raise MalformedResponseError(SERVICE, "clearTaskInstances response has no 'task_instances' list")
        return parse_list(TaskInstance, data["task_instances"], SERVICE, "task_instances")

    def set_task_instance_state(
        self, dag_id: str, dag_run_id: str, task_id: str, new_state: str, *, map_index: int = -1, dry_run: bool
    ) -> list[TaskInstance]:
        """PATCH a task instance state (used only for an approved ``mark_success``)."""
        path = f"{API}/dags/{_q(dag_id)}/dagRuns/{_q(dag_run_id)}/taskInstances/{_q(task_id)}"
        if map_index >= 0:
            path += f"/{map_index}"
        if dry_run:
            path += "/dry_run"
        body = {"new_state": new_state, "include_upstream": False, "include_downstream": False, "include_future": False, "include_past": False}
        data = self._request("PATCH", path, json=body, idempotent=dry_run)
        if isinstance(data, dict) and isinstance(data.get("task_instances"), list):
            return parse_list(TaskInstance, data["task_instances"], SERVICE, "task_instances")
        return [parse(TaskInstance, data, SERVICE)]


def _flatten_log_content(content: Any) -> str:
    """Airflow 3 returns structured log entries; Airflow 2-style servers return text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise MalformedResponseError(SERVICE, "log content must be text or a list")
    lines: list[str] = []
    for item in content:
        if isinstance(item, str):
            lines.append(item)
            continue
        if not isinstance(item, dict):
            continue
        event = str(item.get("event", ""))
        level = str(item.get("level", "")).upper()
        stamp = item.get("timestamp", "")
        prefix = f"[{stamp}] {level} - " if stamp or level else ""
        lines.append(f"{prefix}{event}".rstrip())
        for detail in item.get("error_detail") or []:
            if not isinstance(detail, dict):
                continue
            lines.append("Traceback (most recent call last):")
            for frame in detail.get("frames") or []:
                if isinstance(frame, dict):
                    lines.append(f'  File "{frame.get("filename")}", line {frame.get("lineno")}, in {frame.get("name")}')
            lines.append(f"{detail.get('exc_type', 'Exception')}: {detail.get('exc_value', '')}")
    return "\n".join(lines)
