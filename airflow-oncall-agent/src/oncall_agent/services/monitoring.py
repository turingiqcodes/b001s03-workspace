"""Airflow monitoring and failure detection (FR-004..FR-012, NFR-001/003/004/016).

Three independent pollers, each with its own watermark in ``polling_state``:

=================  ==========================================================  ===============
poller             what it reads                                               watermark field
=================  ==========================================================  ===============
task_instances     failed task instances, all DAGs and runs (``~/~``)          ``end_date``
dag_runs           failed DAG runs                                             ``end_date``
import_errors      DAG import errors                                           ``timestamp``
=================  ==========================================================  ===============

Watermark rules
---------------
* Each poll reads from ``watermark - overlap_seconds`` (or ``now - initial_lookback``
  on the very first run). Re-reading the overlap is harmless: every observation has a
  unique ``event_key`` and is processed once (NFR-005).
* :meth:`MonitoringService.poll` never writes a watermark. The caller processes the
  events and then calls :meth:`MonitoringService.commit`, which advances only pollers
  that (a) polled successfully and (b) had every event recorded (FR-011, NFR-003).
  A failed poll leaves the stored watermark exactly as it was.
* A successful poll advances the watermark to ``max(latest event time, poll start -
  overlap)``, so quiet periods do not make the query window grow forever.
* An event whose log could not be fetched because Airflow was temporarily unavailable
  is *deferred*: the watermark is held just before it so it is read again next cycle.
  After ``MAX_LOG_DEFERRALS`` attempts the event is processed without its log.

Assumption (documented in docs/architecture.md): the agent and Airflow clocks differ
by less than ``overlap_seconds``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import PollingSettings
from ..exceptions import IntegrationError, OncallError
from ..integrations.airflow import AirflowClient
from ..models import AuditEventType, FailureEvent, FailureType
from ..store import Store
from .audit import AuditService

log = logging.getLogger(__name__)

TASK_POLLER = "task_instances"
DAG_RUN_POLLER = "dag_runs"
IMPORT_POLLER = "import_errors"
POLLERS = (TASK_POLLER, DAG_RUN_POLLER, IMPORT_POLLER)

MAX_LOG_DEFERRALS = 3
DAG_RUN_FAILURE_MESSAGE = "DAG run ended in state '{state}' without a failed task instance"


class LogTemporarilyUnavailable(OncallError):
    """The task log could not be read right now; retry the event next cycle."""


@dataclass
class PollerResult:
    name: str
    since: datetime
    ok: bool = True
    error: str | None = None
    events: list[FailureEvent] = field(default_factory=list)
    # Observations that need no incident of their own (failed DAG runs whose failed
    # tasks are reported by the task poller).
    folded: list[FailureEvent] = field(default_factory=list)
    # Latest timestamp seen in this poll, used to advance the watermark.
    latest_seen: datetime | None = None


@dataclass
class PollBatch:
    started_at: datetime
    results: dict[str, PollerResult]

    @property
    def events(self) -> list[tuple[str, FailureEvent]]:
        return [(name, e) for name, r in self.results.items() if r.ok for e in r.events]

    @property
    def failed_pollers(self) -> list[str]:
        return [name for name, r in self.results.items() if not r.ok]


@dataclass(frozen=True)
class ErrorText:
    text: str | None
    source: str  # "log" | "stack_trace" | "message" | "none"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MonitoringService:
    def __init__(
        self,
        airflow: AirflowClient,
        store: Store,
        audit: AuditService,
        settings: PollingSettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.airflow = airflow
        self.store = store
        self.audit = audit
        self.settings = settings
        self._clock = clock or _utcnow
        self._log_deferrals: dict[str, int] = {}

    # ----------------------------------------------------------------- polling

    def _since(self, poller: str, now: datetime) -> datetime:
        watermark = self.store.get_watermark(poller)
        if watermark is None:
            return now - timedelta(minutes=self.settings.initial_lookback_minutes)
        return watermark - timedelta(seconds=self.settings.overlap_seconds)

    def poll(self) -> PollBatch:
        """Run all pollers. One poller failing does not stop the others (NFR-004)."""
        now = self._clock()
        batch = PollBatch(started_at=now, results={})
        for name, fn in ((TASK_POLLER, self._poll_task_instances), (DAG_RUN_POLLER, self._poll_dag_runs), (IMPORT_POLLER, self._poll_import_errors)):
            result = PollerResult(name=name, since=self._since(name, now))
            try:
                fn(result)
            except IntegrationError as exc:
                self._poll_failed(result, f"{type(exc).__name__}: {exc}", retriable=exc.retriable)
            except Exception as exc:  # unexpected bugs must not stop monitoring (NFR-004)
                log.exception("Poller %s crashed", name)
                self._poll_failed(result, f"unexpected {type(exc).__name__}: {exc}", retriable=False)
            batch.results[name] = result
        return batch

    def _poll_failed(self, result: PollerResult, message: str, *, retriable: bool) -> None:
        result.ok, result.error, result.events, result.folded = False, message, [], []
        log.warning("Poll %s failed; watermark kept: %s", result.name, message)
        self.audit.record(
            AuditEventType.POLL_FAILED,
            actor="agent",
            action=f"poll_{result.name}",
            input_summary={"since": result.since.isoformat()},
            output_summary=message,
            success=False,
            details={"retriable": retriable, "watermark_advanced": False},
        )

    def _poll_task_instances(self, result: PollerResult) -> None:
        """FR-006/008: failed task instances across all DAGs and runs."""
        for ti in self.airflow.list_task_instances(states=["failed"], end_date_gte=result.since):
            result.events.append(
                FailureEvent(
                    failure_type=FailureType.TASK_FAILURE,
                    dag_id=ti.dag_id,
                    dag_run_id=ti.dag_run_id,
                    task_id=ti.task_id,
                    map_index=ti.map_index,
                    try_number=ti.try_number,
                    state=ti.state or "failed",
                    logical_date=ti.logical_date,
                    start_date=ti.start_date,
                    end_date=ti.end_date,
                    detected_at=self._clock(),
                )
            )
            result.latest_seen = max(filter(None, (result.latest_seen, ti.end_date)), default=None)

    def _poll_dag_runs(self, result: PollerResult) -> None:
        """FR-005: failed DAG runs. A run with failed tasks is folded into the task
        incidents; only runs that failed *without* a failed task get their own event."""
        for run in self.airflow.list_dag_runs(states=["failed"], end_date_gte=result.since):
            event = FailureEvent(
                failure_type=FailureType.DAG_RUN_FAILURE,
                dag_id=run.dag_id,
                dag_run_id=run.dag_run_id,
                state=run.state,
                logical_date=run.logical_date,
                start_date=run.start_date,
                end_date=run.end_date,
                detected_at=self._clock(),
            )
            result.latest_seen = max(filter(None, (result.latest_seen, run.end_date)), default=None)
            if self.store.is_event_processed(event.event_key):
                continue  # NFR-016: do not query its task instances again
            failed_tasks = self.airflow.list_task_instances(run.dag_id, run.dag_run_id, states=["failed"], max_items=1)
            (result.folded if failed_tasks else result.events).append(event)

    def _poll_import_errors(self, result: PollerResult) -> None:
        """FR-007: DAG import errors (the API has no time filter; filter locally)."""
        for err in self.airflow.list_import_errors():
            if err.timestamp < result.since:
                continue
            result.events.append(
                FailureEvent(
                    failure_type=FailureType.IMPORT_ERROR,
                    dag_id=err.filename,
                    filename=err.filename,
                    state="import_error",
                    error_message=err.stack_trace,
                    end_date=err.timestamp,
                    detected_at=self._clock(),
                )
            )
            result.latest_seen = max(filter(None, (result.latest_seen, err.timestamp)), default=None)

    # ----------------------------------------------------------------- error text for fingerprinting

    def error_text(self, event: FailureEvent) -> ErrorText:
        """The text the fingerprint's error line is extracted from.

        Raises :class:`LogTemporarilyUnavailable` when a task log cannot be read because
        of a transient Airflow problem (the event will be retried next cycle).
        """
        if event.failure_type is FailureType.IMPORT_ERROR:
            return ErrorText(event.error_message, "stack_trace")
        if event.failure_type is FailureType.DAG_RUN_FAILURE:
            return ErrorText(DAG_RUN_FAILURE_MESSAGE.format(state=event.state), "message")

        assert event.dag_run_id and event.task_id
        try_number = max(event.try_number or 1, 1)
        try:
            text = self.airflow.get_task_log(event.dag_id, event.dag_run_id, event.task_id, try_number, event.map_index)
            self._log_deferrals.pop(event.event_key, None)
            return ErrorText(text, "log")
        except IntegrationError as exc:
            attempts = self._log_deferrals.get(event.event_key, 0) + 1
            if exc.retriable and attempts < MAX_LOG_DEFERRALS:
                self._log_deferrals[event.event_key] = attempts
                raise LogTemporarilyUnavailable(f"log for {event.event_key} unavailable (attempt {attempts}): {exc}") from exc
            # Missing log (404) or gave up: proceed without it. FR-028/029: this is
            # recorded as missing evidence, never treated as a cause.
            self._log_deferrals.pop(event.event_key, None)
            self.audit.record(
                AuditEventType.INTEGRATION_FAILURE,
                actor="agent",
                action="airflow.get_task_log",
                input_summary=event.event_key,
                output_summary=str(exc),
                success=False,
                details={"fallback": "fingerprint without log", "attempts": attempts},
            )
            return ErrorText(None, "none")

    # ----------------------------------------------------------------- watermarks

    def commit(
        self,
        batch: PollBatch,
        *,
        deferred: dict[str, list[FailureEvent]] | None = None,
        processing_failed: set[str] | None = None,
    ) -> dict[str, datetime | None]:
        """Advance watermarks for pollers whose poll and processing both succeeded.

        Returns ``{poller: new watermark or None if not advanced}``.
        """
        deferred = deferred or {}
        processing_failed = processing_failed or set()
        advanced: dict[str, datetime | None] = {}
        floor = batch.started_at - timedelta(seconds=self.settings.overlap_seconds)

        for name, result in batch.results.items():
            if not result.ok or name in processing_failed:
                advanced[name] = None  # FR-011: never advance after a failed poll
                continue
            candidate = max(filter(None, (result.latest_seen, floor)))
            held = [e.end_date for e in deferred.get(name, []) if e.end_date is not None]
            if held:
                # Re-read deferred events next time: stay just before the earliest one.
                candidate = min(candidate, min(held) - timedelta(microseconds=1))
            previous = self.store.get_watermark(name)
            if previous is not None and candidate <= previous:
                advanced[name] = None
                continue
            self.store.set_watermark(name, candidate)
            advanced[name] = candidate

        self.audit.record(
            AuditEventType.POLL_COMPLETED,
            actor="agent",
            action="poll_cycle",
            input_summary={n: r.since.isoformat() for n, r in batch.results.items()},
            output_summary={
                "events": {n: len(r.events) for n, r in batch.results.items()},
                "failed_pollers": batch.failed_pollers,
                "watermarks": {n: (w.isoformat() if w else None) for n, w in advanced.items()},
            },
            success=not batch.failed_pollers and not processing_failed,
        )
        return advanced
