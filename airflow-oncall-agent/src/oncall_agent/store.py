"""SQLite state store (SRS Part 7, FR-009/010/019/020/064/065/070/072, NFR-002/005).

Design notes
------------
* One connection per :class:`Store`, shared across threads and serialised with a
  re-entrant lock. The agent is a single process (see docs/architecture.md); the lock
  makes the analysis worker threads safe without a connection pool.
* WAL journal mode + ``synchronous=NORMAL``: durable across process crashes, readers
  do not block the writer. ``foreign_keys=ON`` is enforced on every connection.
* Every state change runs inside :meth:`Store.transaction` (``BEGIN IMMEDIATE``). Any
  exception rolls the whole transaction back and SQLite errors surface as
  :class:`StoreError`.
* Duplicate-execution guards live in the schema, not only in Python:
  - ``retry_attempts.idempotency_key`` is UNIQUE, so the same failed try of a task can
    be retried at most once (FR-065);
  - a partial UNIQUE index allows only one open approval per incident, and
    :meth:`claim_approval` uses a conditional UPDATE so an approved action can be
    claimed for execution exactly once (FR-070).
* Timestamps are stored as ISO-8601 UTC strings with microseconds, so string order is
  time order and range queries can use the indexes.
* Schema changes are numbered migrations applied automatically on open.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .exceptions import DuplicateExecutionError, PolicyDeniedError, StoreError
from .models import (
    ApprovalRecord,
    ApprovalSource,
    ApprovalStatus,
    AuditEvent,
    FailureEvent,
    Incident,
    IncidentStatus,
)

log = logging.getLogger(__name__)


def _enum_list(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_FAILURE_TYPES = _enum_list(["task_failure", "dag_run_failure", "import_error"])
_CLASSIFICATIONS = _enum_list(["retriable", "non_retriable", "uncertain"])
_ACTIONS = _enum_list(["retry_task", "rerun_dag", "mark_success", "manual_investigation", "no_action"])
_INCIDENT_STATUSES = _enum_list([s.value for s in IncidentStatus])
_APPROVAL_STATUSES = _enum_list([s.value for s in ApprovalStatus])
_APPROVAL_SOURCES = _enum_list([s.value for s in ApprovalSource])

RETRY_TRIGGERS = ("automatic", "approved", "mcp")
RETRY_STATUSES = ("clearing", "verifying", "succeeded", "failed", "timed_out", "blocked", "error")
OPEN_RETRY_STATUSES = ("clearing", "verifying")
_OPEN_APPROVALS = _enum_list(["pending", "approved", "executing"])

# --------------------------------------------------------------------------- migrations

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        f"""
        CREATE TABLE incidents (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint             TEXT    NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
            fingerprint_input       TEXT    NOT NULL,
            failure_type            TEXT    NOT NULL CHECK (failure_type IN ({_FAILURE_TYPES})),
            dag_id                  TEXT    NOT NULL,
            task_id                 TEXT,
            dag_run_id              TEXT,
            original_error          TEXT,
            normalized_error        TEXT    NOT NULL,
            classification          TEXT    CHECK (classification IS NULL OR classification IN ({_CLASSIFICATIONS})),
            confidence              REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            root_cause              TEXT,
            evidence                TEXT    NOT NULL DEFAULT '[]',
            suggested_fix           TEXT,
            proposed_action         TEXT    CHECK (proposed_action IS NULL OR proposed_action IN ({_ACTIONS})),
            analysis_source         TEXT    CHECK (analysis_source IS NULL OR analysis_source IN ('ai', 'fallback')),
            suspected_commit        TEXT,
            status                  TEXT    NOT NULL CHECK (status IN ({_INCIDENT_STATUSES})),
            linear_issue_id         TEXT,
            linear_issue_identifier TEXT,
            assignee                TEXT,
            ownership_note          TEXT,
            first_detected_at       TEXT    NOT NULL,
            last_detected_at        TEXT    NOT NULL,
            occurrence_count        INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
            retry_count             INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            cycle_retry_count       INTEGER NOT NULL DEFAULT 0 CHECK (cycle_retry_count >= 0),
            reopened_at             TEXT,
            created_at              TEXT    NOT NULL,
            updated_at              TEXT    NOT NULL
        );
        CREATE INDEX ix_incidents_status ON incidents(status);
        CREATE INDEX ix_incidents_last_detected ON incidents(last_detected_at);
        CREATE INDEX ix_incidents_dag ON incidents(dag_id, task_id);
        CREATE UNIQUE INDEX ux_incidents_linear ON incidents(linear_issue_id) WHERE linear_issue_id IS NOT NULL;

        CREATE TABLE polling_state (
            poller_name TEXT PRIMARY KEY,
            watermark   TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        -- Every failure observation processed, keyed by FailureEvent.event_key (NFR-005).
        CREATE TABLE processed_events (
            event_key    TEXT PRIMARY KEY,
            incident_id  INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
            failure_type TEXT NOT NULL CHECK (failure_type IN ({_FAILURE_TYPES})),
            dag_id       TEXT NOT NULL,
            dag_run_id   TEXT,
            task_id      TEXT,
            try_number   INTEGER,
            event_time   TEXT,
            processed_at TEXT NOT NULL
        );
        CREATE INDEX ix_processed_events_time ON processed_events(processed_at);

        CREATE TABLE retry_attempts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id     INTEGER NOT NULL REFERENCES incidents(id),
            dag_id          TEXT    NOT NULL,
            dag_run_id      TEXT    NOT NULL,
            task_id         TEXT    NOT NULL,
            map_index       INTEGER NOT NULL DEFAULT -1,
            attempt_number  INTEGER NOT NULL CHECK (attempt_number >= 1),
            trigger_type    TEXT    NOT NULL CHECK (trigger_type IN ({_enum_list(list(RETRY_TRIGGERS))})),
            idempotency_key TEXT    NOT NULL UNIQUE,
            status          TEXT    NOT NULL CHECK (status IN ({_enum_list(list(RETRY_STATUSES))})),
            started_at      TEXT    NOT NULL,
            completed_at    TEXT,
            result          TEXT,
            UNIQUE (incident_id, attempt_number)
        );
        CREATE INDEX ix_retry_trigger_time ON retry_attempts(trigger_type, started_at);
        CREATE INDEX ix_retry_status ON retry_attempts(status);

        CREATE TABLE approvals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id      INTEGER NOT NULL REFERENCES incidents(id),
            requested_action TEXT    NOT NULL CHECK (requested_action IN ('rerun_dag', 'mark_success')),
            dag_id           TEXT    NOT NULL,
            dag_run_id       TEXT    NOT NULL,
            task_id          TEXT,
            requested_at     TEXT    NOT NULL,
            status           TEXT    NOT NULL CHECK (status IN ({_APPROVAL_STATUSES})),
            approval_source  TEXT    CHECK (approval_source IS NULL OR approval_source IN ({_APPROVAL_SOURCES})),
            approver         TEXT,
            approved_at      TEXT,
            executed_at      TEXT,
            execution_result TEXT
        );
        CREATE INDEX ix_approvals_incident ON approvals(incident_id, status);
        -- At most one open approval request per incident.
        CREATE UNIQUE INDEX ux_approvals_open ON approvals(incident_id) WHERE status IN ({_OPEN_APPROVALS});

        CREATE TABLE audit_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id    INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
            event_type     TEXT    NOT NULL,
            actor          TEXT    NOT NULL,
            action         TEXT    NOT NULL,
            input_summary  TEXT,
            output_summary TEXT,
            success        INTEGER NOT NULL CHECK (success IN (0, 1)),
            details        TEXT    NOT NULL DEFAULT '{{}}',
            timestamp      TEXT    NOT NULL
        );
        CREATE INDEX ix_audit_incident_time ON audit_events(incident_id, timestamp);
        CREATE INDEX ix_audit_time ON audit_events(timestamp);
        CREATE INDEX ix_audit_type ON audit_events(event_type);
        """,
    ),
    (
        2,
        """
        -- Phase 6: the task try number the retry started from, so verification can tell
        -- "re-ran and failed again" (try advanced) from "never re-ran" (same try).
        ALTER TABLE retry_attempts ADD COLUMN original_try_number INTEGER;
        """,
    ),
    (
        3,
        """
        -- Phase 7: Linear reconciliation and report content.
        ALTER TABLE incidents ADD COLUMN status_reason TEXT;
        ALTER TABLE incidents ADD COLUMN analysis_explanation TEXT;
        ALTER TABLE incidents ADD COLUMN analysis_fallback_reason TEXT;
        ALTER TABLE incidents ADD COLUMN missing_evidence TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE incidents ADD COLUMN context_summary TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE incidents ADD COLUMN linear_synced_status TEXT;
        ALTER TABLE incidents ADD COLUMN linear_synced_occurrences INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX ix_incidents_linear_sync ON incidents(linear_synced_status, status);
        """,
    ),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


# --------------------------------------------------------------------------- helpers


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db_time(value: datetime | None) -> str | None:
    """Canonical UTC text form. Naive datetimes are assumed to be UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def from_db_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_INCIDENT_COLUMNS = [
    "fingerprint", "fingerprint_input", "failure_type", "dag_id", "task_id", "dag_run_id",
    "original_error", "normalized_error", "classification", "confidence", "root_cause",
    "evidence", "suggested_fix", "proposed_action", "analysis_source", "suspected_commit",
    "status", "linear_issue_id", "linear_issue_identifier", "assignee", "ownership_note",
    "first_detected_at", "last_detected_at", "occurrence_count", "retry_count",
    "cycle_retry_count", "reopened_at", "created_at", "updated_at",
    "status_reason", "analysis_explanation", "analysis_fallback_reason", "missing_evidence", "context_summary",
    "linear_synced_status", "linear_synced_occurrences",
]  # fmt: skip
_JSON_FIELDS = {"fingerprint_input", "evidence", "missing_evidence", "context_summary"}
_TIME_FIELDS = {"first_detected_at", "last_detected_at", "reopened_at", "created_at", "updated_at"}


def _incident_to_row(incident: Incident) -> dict[str, Any]:
    data = incident.model_dump(mode="python")
    row: dict[str, Any] = {}
    for col in _INCIDENT_COLUMNS:
        value = data[col]
        if col in _JSON_FIELDS:
            value = json.dumps(value, sort_keys=True, default=str)
        elif col in _TIME_FIELDS:
            value = to_db_time(value)
        elif hasattr(value, "value"):  # enums
            value = value.value
        row[col] = value
    return row


def _row_to_incident(row: sqlite3.Row) -> Incident:
    data = dict(row)
    for col in _JSON_FIELDS:
        data[col] = json.loads(data[col])
    for col in _TIME_FIELDS:
        data[col] = from_db_time(data[col])
    return Incident.model_validate(data)


def _row_to_approval(row: sqlite3.Row) -> ApprovalRecord:
    data = dict(row)
    for col in ("requested_at", "approved_at", "executed_at"):
        data[col] = from_db_time(data[col])
    return ApprovalRecord.model_validate(data)


# --------------------------------------------------------------------------- store


class Store:
    """Persistence for incidents, watermarks, retries, approvals and audit events."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._in_tx = 0

    # ----------------------------------------------------------------- lifecycle

    def open(self) -> "Store":
        """Open the database, creating it and applying migrations if needed."""
        with self._lock:
            if self._conn is not None:
                return self
            try:
                if isinstance(self.path, Path):
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(self.path), timeout=10, isolation_level=None, check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 10000")
                if isinstance(self.path, Path):
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
            except (sqlite3.Error, OSError) as exc:
                raise StoreError(f"Cannot open database {self.path}: {exc}") from exc
            self._conn = conn
            self._migrate()
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "Store":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("Store is not open")
        return self._conn

    def schema_version(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            return int(row["v"] or 0)

    def _migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = self.schema_version()
        if current > SCHEMA_VERSION:
            raise StoreError(
                f"Database schema v{current} is newer than this agent supports (v{SCHEMA_VERSION}); refusing to run"
            )
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            log.info("Applying database migration v%d", version)
            # executescript() would COMMIT implicitly; run statements one by one in a
            # single transaction instead so a failed migration leaves nothing behind.
            with self.transaction() as conn:
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (version, to_db_time(utcnow())))

    # ----------------------------------------------------------------- transactions

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block atomically. Nested calls join the outer transaction."""
        with self._lock:
            conn = self.conn
            if self._in_tx:
                self._in_tx += 1
                try:
                    yield conn
                finally:
                    self._in_tx -= 1
                return
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StoreError(f"Cannot start transaction: {exc}") from exc
            self._in_tx = 1
            try:
                yield conn
            except BaseException as exc:
                self._in_tx = 0
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - rollback after a broken connection
                    log.exception("ROLLBACK failed")
                if isinstance(exc, sqlite3.IntegrityError):
                    raise StoreError(f"Constraint violated: {exc}") from exc
                if isinstance(exc, sqlite3.Error):
                    raise StoreError(f"Database error: {exc}") from exc
                raise
            else:
                self._in_tx = 0
                try:
                    conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    conn.execute("ROLLBACK")
                    raise StoreError(f"Commit failed: {exc}") from exc

    def _query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self.conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise StoreError(f"Query failed: {exc}") from exc

    # ----------------------------------------------------------------- polling state (FR-009/010)

    def get_watermark(self, poller: str) -> datetime | None:
        rows = self._query("SELECT watermark FROM polling_state WHERE poller_name = ?", (poller,))
        return from_db_time(rows[0]["watermark"]) if rows else None

    def set_watermark(self, poller: str, watermark: datetime) -> bool:
        """Store a watermark. It never moves backwards; returns False if it would have."""
        new = to_db_time(watermark)
        with self.transaction() as conn:
            row = conn.execute("SELECT watermark FROM polling_state WHERE poller_name = ?", (poller,)).fetchone()
            if row is not None and row["watermark"] >= new:
                return row["watermark"] == new
            conn.execute(
                "INSERT INTO polling_state (poller_name, watermark, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(poller_name) DO UPDATE SET watermark = excluded.watermark, updated_at = excluded.updated_at",
                (poller, new, to_db_time(utcnow())),
            )
            return True

    def all_watermarks(self) -> dict[str, datetime]:
        return {r["poller_name"]: from_db_time(r["watermark"]) for r in self._query("SELECT * FROM polling_state")}  # type: ignore[misc]

    # ----------------------------------------------------------------- processed events (NFR-005)

    def is_event_processed(self, event_key: str) -> bool:
        return bool(self._query("SELECT 1 FROM processed_events WHERE event_key = ?", (event_key,)))

    def mark_event_processed(self, event: FailureEvent, incident_id: int | None) -> bool:
        """Record an observation. Returns False if it had already been recorded."""
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed_events "
                "(event_key, incident_id, failure_type, dag_id, dag_run_id, task_id, try_number, event_time, processed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_key, incident_id, event.failure_type.value, event.dag_id, event.dag_run_id,
                    event.task_id, event.try_number, to_db_time(event.end_date), to_db_time(utcnow()),
                ),
            )  # fmt: skip
            return cur.rowcount == 1

    # ----------------------------------------------------------------- incidents

    def create_incident(self, incident: Incident) -> Incident:
        """Insert a new incident. A fingerprint that already exists raises StoreError."""
        now = utcnow()
        incident = incident.model_copy(update={"created_at": now, "updated_at": now})
        row = _incident_to_row(incident)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        with self.transaction() as conn:
            cur = conn.execute(f"INSERT INTO incidents ({cols}) VALUES ({marks})", row)
            new_id = cur.lastrowid
        return incident.model_copy(update={"id": new_id})

    def update_incident(self, incident: Incident) -> Incident:
        """Persist every field of an existing incident (by id)."""
        if incident.id is None:
            raise StoreError("Cannot update an incident without an id")
        incident = incident.model_copy(update={"updated_at": utcnow()})
        row = _incident_to_row(incident)
        row.pop("created_at")
        assignments = ", ".join(f"{c} = :{c}" for c in row)
        with self.transaction() as conn:
            cur = conn.execute(f"UPDATE incidents SET {assignments} WHERE id = :id", {**row, "id": incident.id})
            if cur.rowcount != 1:
                raise StoreError(f"Incident {incident.id} does not exist")
        return incident

    def get_incident(self, incident_id: int) -> Incident | None:
        rows = self._query("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        return _row_to_incident(rows[0]) if rows else None

    def get_incident_by_fingerprint(self, fingerprint: str) -> Incident | None:
        rows = self._query("SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,))
        return _row_to_incident(rows[0]) if rows else None

    def get_incident_by_linear_issue(self, linear_issue_id: str) -> Incident | None:
        rows = self._query("SELECT * FROM incidents WHERE linear_issue_id = ?", (linear_issue_id,))
        return _row_to_incident(rows[0]) if rows else None

    def list_incidents_needing_linear_sync(self, limit: int = 200) -> list[Incident]:
        """Analysed incidents with no Linear issue yet, or whose Linear issue is behind
        the local status / occurrence count (reconciled every cycle)."""
        rows = self._query(
            "SELECT * FROM incidents WHERE "
            "(linear_issue_id IS NULL AND classification IS NOT NULL) OR "
            "(linear_issue_id IS NOT NULL AND (linear_synced_status IS NULL OR linear_synced_status != status "
            "OR linear_synced_occurrences < occurrence_count)) "
            "ORDER BY updated_at LIMIT ?",
            (limit,),
        )
        return [_row_to_incident(r) for r in rows]

    def list_incidents(self, statuses: list[IncidentStatus] | None = None, limit: int = 100) -> list[Incident]:
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            rows = self._query(
                f"SELECT * FROM incidents WHERE status IN ({marks}) ORDER BY last_detected_at DESC LIMIT ?",
                (*[s.value for s in statuses], limit),
            )
        else:
            rows = self._query("SELECT * FROM incidents ORDER BY last_detected_at DESC LIMIT ?", (limit,))
        return [_row_to_incident(r) for r in rows]

    # ----------------------------------------------------------------- retry attempts (FR-064/065)

    def start_retry_attempt(
        self,
        *,
        incident_id: int,
        dag_id: str,
        dag_run_id: str,
        task_id: str,
        map_index: int,
        trigger_type: str,
        idempotency_key: str,
        original_try_number: int | None = None,
        max_incident_retries: int | None = None,
        max_global_retries: int | None = None,
        global_window_start: datetime | None = None,
    ) -> int:
        """Atomically reserve a retry and bump the incident's counters.

        Raises :class:`DuplicateExecutionError` if a retry with the same idempotency key
        already exists (FR-065). Automatic retries increment both ``retry_count`` and
        ``cycle_retry_count``; other triggers only the attempt numbering.

        For automatic retries the limits are re-checked *inside this transaction*
        (``max_incident_retries`` against the incident's ``cycle_retry_count``,
        ``max_global_retries`` against automatic retries since ``global_window_start``),
        so concurrent workers can never exceed them (FR-050). A limit breach raises
        :class:`PolicyDeniedError` and nothing is written.
        """
        if trigger_type not in RETRY_TRIGGERS:
            raise StoreError(f"Unknown retry trigger {trigger_type!r}")
        try:
            with self.transaction() as conn:
                if conn.execute("SELECT 1 FROM retry_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone():
                    raise DuplicateExecutionError(f"Retry already recorded for {idempotency_key}")
                if trigger_type == "automatic":
                    if max_incident_retries is None or max_global_retries is None or global_window_start is None:
                        raise PolicyDeniedError("automatic retry reserved without limits (fail closed)")
                    inc = conn.execute("SELECT cycle_retry_count FROM incidents WHERE id = ?", (incident_id,)).fetchone()
                    if inc is None:
                        raise StoreError(f"Incident {incident_id} does not exist")
                    if inc["cycle_retry_count"] >= max_incident_retries:
                        raise PolicyDeniedError(f"incident retry limit reached ({inc['cycle_retry_count']}/{max_incident_retries})")
                    recent = conn.execute(
                        "SELECT COUNT(*) AS n FROM retry_attempts WHERE trigger_type = 'automatic' AND started_at >= ?",
                        (to_db_time(global_window_start),),
                    ).fetchone()["n"]
                    if recent >= max_global_retries:
                        raise PolicyDeniedError(f"global retry limit reached ({recent}/{max_global_retries} in the last hour)")
                row = conn.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS n FROM retry_attempts WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                cur = conn.execute(
                    "INSERT INTO retry_attempts (incident_id, dag_id, dag_run_id, task_id, map_index, attempt_number, "
                    "trigger_type, idempotency_key, status, started_at, original_try_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'clearing', ?, ?)",
                    (incident_id, dag_id, dag_run_id, task_id, map_index, row["n"], trigger_type, idempotency_key, to_db_time(utcnow()), original_try_number),
                )
                if trigger_type == "automatic":
                    updated = conn.execute(
                        "UPDATE incidents SET retry_count = retry_count + 1, cycle_retry_count = cycle_retry_count + 1, "
                        "updated_at = ? WHERE id = ?",
                        (to_db_time(utcnow()), incident_id),
                    )
                    if updated.rowcount != 1:
                        raise StoreError(f"Incident {incident_id} does not exist")
                return int(cur.lastrowid)
        except StoreError as exc:
            # A concurrent insert of the same key loses on the UNIQUE constraint.
            if "idempotency_key" in str(exc):
                raise DuplicateExecutionError(f"Retry already recorded for {idempotency_key}") from exc
            raise

    def finish_retry_attempt(self, retry_id: int, status: str, result: str | None = None) -> None:
        if status not in RETRY_STATUSES:
            raise StoreError(f"Unknown retry status {status!r}")
        completed = None if status in OPEN_RETRY_STATUSES else to_db_time(utcnow())
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE retry_attempts SET status = ?, result = COALESCE(?, result), completed_at = ? WHERE id = ?",
                (status, result, completed, retry_id),
            )
            if cur.rowcount != 1:
                raise StoreError(f"Retry attempt {retry_id} does not exist")

    def get_retry_attempt(self, retry_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM retry_attempts WHERE id = ?", (retry_id,))
        return dict(rows[0]) if rows else None

    def list_retry_attempts(self, incident_id: int | None = None, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM retry_attempts WHERE 1 = 1", []
        if incident_id is not None:
            sql += " AND incident_id = ?"
            params.append(incident_id)
        if statuses:
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        return [dict(r) for r in self._query(sql + " ORDER BY id", tuple(params))]

    def count_automatic_retries_since(self, since: datetime) -> int:
        """Automatic retries started at or after ``since`` (global hourly limit, FR-050)."""
        rows = self._query(
            "SELECT COUNT(*) AS n FROM retry_attempts WHERE trigger_type = 'automatic' AND started_at >= ?",
            (to_db_time(since),),
        )
        return int(rows[0]["n"])

    # ----------------------------------------------------------------- approvals (FR-066..072)

    def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        """Create a pending approval request. Only one open request per incident."""
        if record.status is not ApprovalStatus.PENDING:
            raise StoreError("New approval requests must be pending")
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO approvals (incident_id, requested_action, dag_id, dag_run_id, task_id, requested_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (record.incident_id, record.requested_action.value, record.dag_id, record.dag_run_id, record.task_id, to_db_time(record.requested_at)),
            )
        return record.model_copy(update={"id": cur.lastrowid})

    def get_approval(self, approval_id: int) -> ApprovalRecord | None:
        rows = self._query("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return _row_to_approval(rows[0]) if rows else None

    def get_open_approval(self, incident_id: int) -> ApprovalRecord | None:
        rows = self._query(
            f"SELECT * FROM approvals WHERE incident_id = ? AND status IN ({_OPEN_APPROVALS})", (incident_id,)
        )
        return _row_to_approval(rows[0]) if rows else None

    def list_approvals(self, incident_id: int | None = None, statuses: list[ApprovalStatus] | None = None) -> list[ApprovalRecord]:
        sql, params = "SELECT * FROM approvals WHERE 1 = 1", []
        if incident_id is not None:
            sql += " AND incident_id = ?"
            params.append(incident_id)
        if statuses:
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(s.value for s in statuses)
        return [_row_to_approval(r) for r in self._query(sql + " ORDER BY id", tuple(params))]

    def record_approval(self, approval_id: int, *, source: ApprovalSource, approver: str, approved_at: datetime) -> bool:
        """pending -> approved. Returns False if the request was not pending (already
        approved, executed, or rejected), so a repeated approval changes nothing."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = 'approved', approval_source = ?, approver = ?, approved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (source.value, approver, to_db_time(approved_at), approval_id),
            )
            return cur.rowcount == 1

    def claim_approval(self, approval_id: int) -> bool:
        """approved -> executing. Exactly one caller can win this (FR-070)."""
        with self.transaction() as conn:
            cur = conn.execute("UPDATE approvals SET status = 'executing' WHERE id = ? AND status = 'approved'", (approval_id,))
            return cur.rowcount == 1

    def complete_approval(self, approval_id: int, *, succeeded: bool, result: str) -> None:
        """executing -> executed | failed."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = ?, executed_at = ?, execution_result = ? WHERE id = ? AND status = 'executing'",
                ("executed" if succeeded else "failed", to_db_time(utcnow()), result, approval_id),
            )
            if cur.rowcount != 1:
                raise StoreError(f"Approval {approval_id} is not executing")

    def reject_approval(self, approval_id: int, reason: str) -> bool:
        """pending -> rejected (e.g. incident resolved before anyone approved)."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = 'rejected', execution_result = ? WHERE id = ? AND status = 'pending'",
                (reason, approval_id),
            )
            return cur.rowcount == 1

    # ----------------------------------------------------------------- audit events

    def insert_audit_event(self, event: AuditEvent) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO audit_events (incident_id, event_type, actor, action, input_summary, output_summary, success, details, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.incident_id, event.event_type.value, event.actor, event.action, event.input_summary,
                    event.output_summary, int(event.success), json.dumps(event.details, default=str, sort_keys=True),
                    to_db_time(event.timestamp),
                ),
            )  # fmt: skip
            return int(cur.lastrowid)

    def list_audit_events(self, incident_id: int | None = None, limit: int = 1000) -> list[AuditEvent]:
        if incident_id is None:
            rows = self._query("SELECT * FROM audit_events ORDER BY timestamp, id LIMIT ?", (limit,))
        else:
            rows = self._query("SELECT * FROM audit_events WHERE incident_id = ? ORDER BY timestamp, id LIMIT ?", (incident_id, limit))
        events = []
        for r in rows:
            data = dict(r)
            data["success"] = bool(data["success"])
            data["details"] = json.loads(data["details"])
            data["timestamp"] = from_db_time(data["timestamp"])
            events.append(AuditEvent.model_validate(data))
        return events


def _split_sql(script: str) -> list[str]:
    """Split a migration script on semicolons, dropping comment-only lines.
    Migrations must not contain ';' inside string literals."""
    statements = []
    for chunk in script.split(";"):
        body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            statements.append(body)
    return statements
