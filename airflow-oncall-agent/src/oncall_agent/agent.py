"""The agent: coordinates the incident lifecycle (SRS §5.1, §5.2 "Agent").

One cycle:

    poll Airflow → for each failure: error text → fingerprint → record incident
    → advance watermarks only for pollers whose events were all recorded
    → verify retries started in earlier cycles (resolve / escalate / keep waiting)
    → hand new / reopened incidents to the analysis workers:
         investigate (collect context) → analyse (AI provider, else fallback)
         → policy decision → auto retry | approval request | escalation
    → check pending approvals in Linear (validate, execute once)
    → reconcile Linear issues (create / update / comment) for anything that changed

Analysis is kept off the polling path (NFR-018): incidents are processed by a bounded
thread pool (``analysis.worker_threads``). In continuous mode (``run``) a cycle does not
wait for analyses; finished ones are collected by later cycles. ``run_once(wait=True)``
(used by the ``once`` command and tests) waits for the analyses it started.

Robustness: an incident is never submitted twice at the same time, and any incident left
open/investigating (crash, restart, database error — analysed or not) is picked up again
by the next cycle; an existing analysis is reused rather than repeated.
Linear and approval steps are isolated too: a Linear outage is audited and retried next
cycle without affecting monitoring.

Failure isolation (NFR-004): one bad event holds back only its own poller's watermark;
one failed analysis job affects only its incident; an exception escaping a whole cycle is
logged and audited and the loop keeps running.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .analysis import AnalysisService
from .config import Settings
from .context import ContextCollector, InvestigationContext
from .exceptions import ConfigError, OncallError
from .factory import Integrations
from .fingerprint import fingerprint_error
from .models import AnalysisResult, AuditEventType, FailureEvent, Incident, IncidentStatus
from .policy import PolicyDecision, PolicyEngine
from .providers.base import AIProvider
from .services.audit import AuditService
from .ownership import OwnershipResolver
from .services.approval import ApprovalOutcome, ApprovalService
from .services.incident_manager import IncidentManager, LinearIncidentService, LinearSyncReport, RecordOutcome
from .services.investigation import InvestigationService
from .services.monitoring import LogTemporarilyUnavailable, MonitoringService
from .services.recovery import RecoveryResult, RecoveryService, VerificationResult
from .store import Store

log = logging.getLogger(__name__)

# Incidents in these states still need analysis and/or a policy decision.
UNDECIDED_STATUSES = [IncidentStatus.OPEN, IncidentStatus.INVESTIGATING]


@dataclass
class IncidentWork:
    """Outcome of investigating and analysing one incident in a worker."""

    incident_id: int
    context: InvestigationContext | None = None
    analysis: AnalysisResult | None = None
    decision: PolicyDecision | None = None
    recovery: RecoveryResult | None = None
    error: str | None = None


@dataclass
class CycleReport:
    started_at: datetime
    failed_pollers: list[str] = field(default_factory=list)
    outcomes: Counter = field(default_factory=Counter)
    folded: int = 0
    deferred: int = 0
    errors: list[str] = field(default_factory=list)
    watermarks: dict[str, datetime | None] = field(default_factory=dict)
    # Incidents that are new or reopened in this cycle.
    to_investigate: list[Incident] = field(default_factory=list)
    # Error text already downloaded at detection, reused by investigation (NFR-016).
    known_logs: dict[int, str] = field(default_factory=dict, repr=False)
    submitted: list[int] = field(default_factory=list)
    contexts: dict[int, InvestigationContext] = field(default_factory=dict, repr=False)
    analyses: dict[int, AnalysisResult] = field(default_factory=dict, repr=False)
    decisions: dict[int, PolicyDecision] = field(default_factory=dict, repr=False)
    recoveries: dict[int, RecoveryResult] = field(default_factory=dict, repr=False)
    verifications: list[VerificationResult] = field(default_factory=list, repr=False)
    approvals: list[ApprovalOutcome] = field(default_factory=list, repr=False)
    linear: LinearSyncReport | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return not self.failed_pollers and not self.errors

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.outcomes.items())]
        parts += [f"folded={self.folded}", f"deferred={self.deferred}", f"analysed={len(self.analyses)}"]
        decided = Counter(d.decision.value for d in self.decisions.values())
        parts += [f"{k}={v}" for k, v in sorted(decided.items())]
        verified = Counter(v.outcome for v in self.verifications if v.outcome != "pending")
        parts += [f"retry_{k}={v}" for k, v in sorted(verified.items())]
        approved = Counter(a.status for a in self.approvals if a.status not in ("waiting",))
        parts += [f"approval_{k}={v}" for k, v in sorted(approved.items())]
        if self.linear is not None:
            parts.append(f"linear_created={len(self.linear.created)} linear_updated={len(self.linear.updated)}")
            if self.linear.failed:
                parts.append(f"linear_failed={len(self.linear.failed)}")
        if self.failed_pollers:
            parts.append(f"failed_pollers={','.join(self.failed_pollers)}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return " ".join(parts)


class Agent:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        audit: AuditService,
        integrations: Integrations,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.audit = audit
        self.integrations = integrations
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.monitoring = MonitoringService(integrations.airflow, store, audit, settings.polling, clock=self._clock)
        self.incidents = IncidentManager(store, audit, clock=self._clock)
        self.collector = ContextCollector(integrations.airflow, integrations.github, settings.github, settings.ai, clock=self._clock)
        self.investigation = InvestigationService(self.collector, store, audit)
        self.analysis = AnalysisService(self._provider(integrations), store, audit)
        self.policy = PolicyEngine(settings.policy)
        self.recovery = RecoveryService(integrations.airflow, store, audit, self.policy, settings.policy, clock=self._clock)
        self.ownership = OwnershipResolver(integrations.linear, settings.ownership.user_map, settings.linear.default_assignee)
        self.linear_sync = LinearIncidentService(integrations.linear, store, audit, settings, self.ownership)
        self.approvals = ApprovalService(integrations.linear, store, audit, self.recovery, settings)

        self._executor = ThreadPoolExecutor(max_workers=settings.analysis.worker_threads, thread_name_prefix="oncall-analysis")
        self._inflight: dict[int, Future[IncidentWork]] = {}
        self._inflight_lock = threading.Lock()

    @staticmethod
    def _provider(integrations: Integrations) -> AIProvider:
        if integrations.ai is not None:
            return integrations.ai
        if integrations.mode == "mock":
            from .providers.mock import MockAIProvider

            return MockAIProvider()
        raise ConfigError("No AI provider configured")

    # ----------------------------------------------------------------- one cycle

    def run_once(self, *, wait_for_analysis: bool = True, timeout: float | None = None) -> CycleReport:
        batch = self.monitoring.poll()
        report = CycleReport(started_at=batch.started_at, failed_pollers=batch.failed_pollers)
        deferred: dict[str, list[FailureEvent]] = {}
        processing_failed: set[str] = set()

        for poller, result in batch.results.items():
            if not result.ok:
                continue
            for event in result.folded:
                try:
                    self.incidents.mark_event_without_incident(event, "failed tasks reported separately")
                    report.folded += 1
                except OncallError as exc:
                    processing_failed.add(poller)
                    report.errors.append(f"{event.event_key}: {exc}")
            for event in result.events:
                try:
                    self._handle_event(event, report)
                except LogTemporarilyUnavailable as exc:
                    log.info("%s", exc)
                    deferred.setdefault(poller, []).append(event)
                    report.deferred += 1
                except Exception as exc:  # isolate one bad event (NFR-004)
                    log.exception("Failed to process %s", event.event_key)
                    processing_failed.add(poller)
                    report.errors.append(f"{event.event_key}: {type(exc).__name__}: {exc}")
                    self.audit.record(
                        AuditEventType.INTEGRATION_FAILURE if isinstance(exc, OncallError) else AuditEventType.POLL_FAILED,
                        actor="agent",
                        action="process_event",
                        input_summary=event.event_key,
                        output_summary=f"{type(exc).__name__}: {exc}",
                        success=False,
                        details={"watermark_held": poller},
                    )

        report.watermarks = self.monitoring.commit(batch, deferred=deferred, processing_failed=processing_failed)

        try:  # FR-060..063: outcomes of retries started earlier
            report.verifications = self.recovery.verify_pending()
        except Exception as exc:
            log.exception("Retry verification failed")
            report.errors.append(f"verify retries: {type(exc).__name__}: {exc}")

        # Hand work to the analysis workers (never blocks polling state above).
        for incident in report.to_investigate:
            if self._submit(incident, report.known_logs.get(incident.id or -1)):
                report.submitted.append(incident.id)  # type: ignore[arg-type]
        self._requeue_unanalysed(report)

        if wait_for_analysis:
            self.drain(report, timeout=timeout)
        else:
            self._collect_finished(report)
        self._linear_steps(report)
        log.info("Cycle finished: %s", report.summary())
        return report

    def _linear_steps(self, report: CycleReport) -> None:
        try:  # FR-068..072
            report.approvals = self.approvals.check_pending()
        except Exception as exc:
            log.exception("Approval check failed")
            report.errors.append(f"approvals: {type(exc).__name__}: {exc}")
        try:  # FR-074..077
            report.linear = self.linear_sync.reconcile()
            report.errors.extend(report.linear.failed)
        except Exception as exc:
            log.exception("Linear reconcile failed")
            report.errors.append(f"linear: {type(exc).__name__}: {exc}")

    def _handle_event(self, event: FailureEvent, report: CycleReport) -> None:
        if self.store.is_event_processed(event.event_key):
            report.outcomes[RecordOutcome.ALREADY_PROCESSED.value] += 1
            return  # NFR-016: no log download for something already handled
        error = self.monitoring.error_text(event)
        fp = fingerprint_error(event.dag_id, event.fingerprint_task_id, error.text, error_source=error.source)
        result = self.incidents.record_failure(event, fp)
        report.outcomes[result.outcome.value] += 1
        if result.needs_investigation and result.incident is not None:
            report.to_investigate.append(result.incident)
            if error.text is not None and result.incident.id is not None:
                report.known_logs[result.incident.id] = error.text

    # ----------------------------------------------------------------- analysis workers (NFR-018)

    def _submit(self, incident: Incident, known_log: str | None) -> bool:
        assert incident.id is not None
        with self._inflight_lock:
            if incident.id in self._inflight:
                return False
            self._inflight[incident.id] = self._executor.submit(self._process, incident, known_log)
            return True

    def _process(self, incident: Incident, known_log: str | None) -> IncidentWork:
        work = IncidentWork(incident_id=incident.id or -1)
        try:
            current = self.store.get_incident(work.incident_id) or incident
            if current.classification is None:
                work.context = self.investigation.investigate(current, known_log=known_log)
                current = self.store.get_incident(work.incident_id) or current
                work.analysis = self.analysis.analyze(current, work.context)
            # FR-041: the analysis is only a recommendation; the policy decides.
            work.decision, work.recovery = self.recovery.decide_and_act(current)
        except Exception as exc:  # e.g. database failure; the incident is requeued next cycle
            log.exception("Processing incident %s failed", incident.id)
            work.error = f"incident {incident.id}: {type(exc).__name__}: {exc}"
        return work

    def _requeue_unanalysed(self, report: CycleReport) -> None:
        """Pick up incidents that never got an analysis or decision (crash, restart, DB error)."""
        try:
            pending = self.store.list_incidents(UNDECIDED_STATUSES, limit=500)
        except OncallError as exc:
            report.errors.append(f"requeue scan: {exc}")
            return
        for incident in pending:
            if self._submit(incident, None):
                report.submitted.append(incident.id)  # type: ignore[arg-type]

    def _take(self, futures: list[tuple[int, Future[IncidentWork]]], report: CycleReport) -> None:
        for incident_id, future in futures:
            with self._inflight_lock:
                if self._inflight.get(incident_id) is future:
                    del self._inflight[incident_id]
            work = future.result()
            if work.context is not None:
                report.contexts[incident_id] = work.context
            if work.analysis is not None:
                report.analyses[incident_id] = work.analysis
            if work.decision is not None:
                report.decisions[incident_id] = work.decision
            if work.recovery is not None:
                report.recoveries[incident_id] = work.recovery
            if work.error:
                report.errors.append(work.error)

    def _collect_finished(self, report: CycleReport) -> None:
        with self._inflight_lock:
            done = [(i, f) for i, f in self._inflight.items() if f.done()]
        self._take(done, report)

    def drain(self, report: CycleReport | None = None, *, timeout: float | None = None) -> CycleReport:
        """Wait for in-flight analyses (all of them, or until ``timeout``)."""
        report = report or CycleReport(started_at=self._clock())
        with self._inflight_lock:
            futures = list(self._inflight.items())
        finished, _ = wait([f for _, f in futures], timeout=timeout)
        self._take([(i, f) for i, f in futures if f in finished], report)
        return report

    @property
    def inflight(self) -> list[int]:
        with self._inflight_lock:
            return sorted(self._inflight)

    def shutdown(self, *, wait_for_analysis: bool = True) -> None:
        self._executor.shutdown(wait=wait_for_analysis, cancel_futures=not wait_for_analysis)

    # ----------------------------------------------------------------- continuous monitoring

    def run(self, stop: threading.Event, *, max_cycles: int | None = None) -> int:
        """Poll every ``POLL_INTERVAL_SECONDS`` until ``stop`` is set. Returns the number
        of cycles run. A crashing cycle is logged and the loop continues (NFR-004)."""
        interval = self.settings.polling.interval_seconds
        cycles = 0
        log.info("Monitoring started (interval %ss)", interval)
        try:
            while not stop.is_set():
                try:
                    self.run_once(wait_for_analysis=False)
                except Exception as exc:
                    log.exception("Monitoring cycle failed")
                    self.audit.record(
                        AuditEventType.POLL_FAILED, actor="agent", action="run_cycle",
                        output_summary=f"{type(exc).__name__}: {exc}", success=False,
                    )  # fmt: skip
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                stop.wait(interval)
        finally:
            self.drain()
            log.info("Monitoring stopped after %d cycle(s)", cycles)
        return cycles
