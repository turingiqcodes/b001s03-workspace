"""Recovery: applying policy decisions and the ONLY place that writes to Airflow
(FR-041, FR-050..065, FR-066/067/070/071 execution side, Part 10).

Automatic retry (``auto_retry``)
--------------------------------
1. Re-evaluate the policy on the freshly loaded incident (never trust the caller).
2. Re-read the task instance; it must still be ``failed``.
3. **Dry-run** ``clearTaskInstances`` for the task + downstream, ``only_failed``.
4. Validate every task instance in the preview (fail closed, ``UnsafeClearError``):
   same DAG and run (FR-059); state ``failed``/``upstream_failed``, never ``success``
   (FR-058); the target is present and ``failed``; every other one is an
   ``upstream_failed`` child (FR-057).
5. Reserve the retry in SQLite: idempotency key ``auto:<dag>:<run>:<task>:<map>:<try>``
   (FR-065), incident and global limits re-checked in the same transaction (FR-050).
6. Clear **exactly the validated task ids** (``include_downstream=False``,
   ``only_failed=True``). The real clear is never auto-repeated.
7. Mark the attempt ``verifying``; the incident becomes ``retrying``.

Verification (``verify_pending``, every cycle, non-blocking)
------------------------------------------------------------
* task ``success``                         → attempt succeeded, incident **resolved** (FR-061)
* task failed again with a newer try       → attempt failed, incident **escalated** (FR-063)
* still running / not yet re-run after ``retry_verify_timeout_minutes`` (30)
                                           → attempt timed_out, incident **escalated** (FR-062)
* Airflow unreadable                        → keep verifying; the timeout still applies

Approved restricted actions (``execute_approved``; validation of the approval itself is
the approval service's job, Phase 7)
------------------------------------------------------------------------------------
* claimed exactly once (``Store.claim_approval``, FR-070);
* ``rerun_dag`` (decision A1): clear only the ``failed``/``upstream_failed`` instances of
  that DAG run, same dry-run validation, never successful tasks;
* ``mark_success``: dry-run first, must touch exactly the one failed target instance.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import PolicySettings
from ..exceptions import (
    AirflowError,
    DuplicateExecutionError,
    OncallError,
    PolicyDeniedError,
    UnsafeClearError,
)
from ..integrations.airflow import AirflowClient, TaskInstance
from ..models import (
    DAG_RUN_TASK_ID,
    ApprovalRecord,
    ApprovalStatus,
    AuditEventType,
    Incident,
    IncidentStatus,
    ProposedAction,
)
from ..policy import RETRYABLE_STATUSES, Decision, PolicyDecision, PolicyEngine
from ..store import Store, from_db_time
from .audit import AuditService

log = logging.getLogger(__name__)

CLEARABLE_STATES = frozenset({"failed", "upstream_failed"})
FAILED_AGAIN_STATES = frozenset({"failed", "upstream_failed"})


@dataclass
class RecoveryResult:
    status: str  # cleared | denied | blocked | duplicate | skipped | error | requested | escalated | resolved | executed | failed
    detail: str
    retry_id: int | None = None
    cleared: list[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    retry_id: int
    incident_id: int
    outcome: str  # succeeded | failed | timed_out | pending
    detail: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryService:
    def __init__(
        self,
        airflow: AirflowClient,
        store: Store,
        audit: AuditService,
        policy: PolicyEngine,
        settings: PolicySettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.airflow = airflow
        self.store = store
        self.audit = audit
        self.policy = policy
        self.settings = settings
        self._clock = clock or _utcnow

    # ================================================================= decisions

    def evaluate(self, incident: Incident) -> PolicyDecision:
        """Evaluate and audit the policy decision for the current incident state (FR-053)."""
        window_start = self._clock() - timedelta(hours=1)
        try:
            global_count: int | None = self.store.count_automatic_retries_since(window_start)
        except OncallError:
            global_count = None  # unknown → the global-limit check fails (FR-054)
        decision = self.policy.evaluate(incident, global_count)
        self.audit.record(
            AuditEventType.POLICY_DECISION,
            actor="policy",
            action="evaluate",
            incident_id=incident.id,
            input_summary={
                "classification": incident.classification.value if incident.classification else None,
                "confidence": incident.confidence,
                "proposed_action": incident.proposed_action.value if incident.proposed_action else None,
                "analysis_source": incident.analysis_source.value if incident.analysis_source else None,
                "cycle_retry_count": incident.cycle_retry_count,
                "global_retries_last_hour": global_count,
            },
            output_summary=f"{decision.decision.value}: {'; '.join(decision.reasons)}",
            success=True,
            details=decision.to_audit(),
        )
        return decision

    def decide_and_act(self, incident: Incident) -> tuple[PolicyDecision, RecoveryResult]:
        """Evaluate the policy and carry out the decision (called after analysis)."""
        current = self._reload(incident)
        decision = self.evaluate(current)
        if decision.decision is Decision.AUTO_RETRY:
            result = self.auto_retry(current)
        elif decision.decision is Decision.REQUEST_APPROVAL:
            result = self.request_approval(current, decision.action)  # type: ignore[arg-type]
        elif decision.decision is Decision.NO_ACTION:
            # Never silently close: the AI's "no action" is left for a human to confirm.
            result = self.escalate(current, ["the analysis proposed no action; left for human review"])
        else:
            result = self.escalate(current, list(decision.reasons))
        return decision, result

    # ================================================================= automatic retry

    def auto_retry(self, incident: Incident) -> RecoveryResult:
        incident = self._reload(incident)
        if incident.status not in RETRYABLE_STATUSES:
            # A duplicate request (retry already running, awaiting approval, escalated or
            # resolved): refuse without changing the incident.
            self.audit.record(AuditEventType.RETRY_BLOCKED, actor="agent", action="auto_retry", incident_id=incident.id,
                              output_summary=f"refused: incident is {incident.status.value}", success=False)  # fmt: skip
            return RecoveryResult("duplicate", f"incident is {incident.status.value}; no new retry")
        decision = self.policy.evaluate(incident, self._global_count())
        if not decision.auto_retry_allowed:
            # Defence in depth: whoever called us, the policy must still allow it now.
            return self._refuse(incident, "denied", "; ".join(decision.reasons))
        dag_id, run_id, task_id = incident.dag_id, incident.dag_run_id, incident.task_id
        assert run_id and task_id  # guaranteed by the task_failure check

        try:
            target = self.airflow.get_task_instance(dag_id, run_id, task_id)
        except AirflowError as exc:
            return self._refuse(incident, "error", f"could not read the task state before retrying: {exc}")
        if target.state != "failed":
            if target.state == "success":
                self.resolve(incident, f"task {task_id} is already successful; no retry needed")
                return RecoveryResult("resolved", "task already successful")
            return self._refuse(incident, "skipped", f"task {task_id} is now '{target.state}', not failed; not retried")

        try:
            plan = self._safe_clear_plan(dag_id, run_id, [task_id], target_ids={task_id}, include_downstream=True, mode="task")
        except UnsafeClearError as exc:
            return self._blocked(incident, str(exc))
        except AirflowError as exc:
            return self._refuse(incident, "error", f"dry-run clear failed: {exc}")

        key = f"auto:{dag_id}:{run_id}:{task_id}:{target.map_index}:{target.try_number}"
        try:
            retry_id = self.store.start_retry_attempt(
                incident_id=incident.id, dag_id=dag_id, dag_run_id=run_id, task_id=task_id, map_index=target.map_index,  # type: ignore[arg-type]
                trigger_type="automatic", idempotency_key=key, original_try_number=target.try_number,
                max_incident_retries=self.settings.max_auto_retries, max_global_retries=self.settings.global_max_retries_per_hour,
                global_window_start=self._clock() - timedelta(hours=1),
            )  # fmt: skip
        except DuplicateExecutionError as exc:
            self.audit.record(AuditEventType.RETRY_BLOCKED, actor="agent", action="auto_retry", incident_id=incident.id,
                              output_summary=f"duplicate retry refused: {exc}", success=False, details={"idempotency_key": key})  # fmt: skip
            return RecoveryResult("duplicate", str(exc))
        except PolicyDeniedError as exc:
            return self._refuse(incident, "denied", f"automatic retry refused at reservation: {exc}")

        return self._clear(incident, retry_id, dag_id, run_id, plan, trigger="automatic")

    # ================================================================= approvals

    def request_approval(self, incident: Incident, action: ProposedAction) -> RecoveryResult:
        """Record a pending approval request for a restricted action (FR-055/066/067)."""
        existing = self.store.get_open_approval(incident.id)  # type: ignore[arg-type]
        if existing is not None:
            return RecoveryResult("requested", f"approval {existing.id} for {existing.requested_action.value} already open")
        record = self.store.create_approval(
            ApprovalRecord(incident_id=incident.id, requested_action=action, dag_id=incident.dag_id,  # type: ignore[arg-type]
                           dag_run_id=incident.dag_run_id or "", task_id=incident.task_id, requested_at=self._clock())  # fmt: skip
        )
        self._set_status(incident, IncidentStatus.AWAITING_APPROVAL, f"{action.value} proposed; waiting for a trusted approver")
        self.audit.record(
            AuditEventType.APPROVAL_REQUESTED, actor="policy", action=f"request_{action.value}", incident_id=incident.id,
            input_summary={"dag_id": record.dag_id, "dag_run_id": record.dag_run_id, "task_id": record.task_id},
            output_summary=f"approval {record.id} pending for {action.value}", details={"approval_id": record.id},
        )  # fmt: skip
        return RecoveryResult("requested", f"approval {record.id} requested for {action.value}")

    def execute_approved(self, approval_id: int) -> RecoveryResult:
        """Execute an approved restricted action exactly once (FR-070).

        The caller (approval service) must already have validated the approval and the
        approver (FR-071, NFR-012); this method re-checks only what it can see itself.
        """
        approval = self.store.get_approval(approval_id)
        if approval is None or approval.status is not ApprovalStatus.APPROVED:
            return RecoveryResult("denied", f"approval {approval_id} is not in the approved state")
        if not self.store.claim_approval(approval_id):
            self.audit.record(AuditEventType.RETRY_BLOCKED, actor="agent", action=f"execute_{approval.requested_action.value}",
                              incident_id=approval.incident_id, output_summary=f"approval {approval_id} already claimed", success=False)  # fmt: skip
            return RecoveryResult("duplicate", f"approval {approval_id} was already executed or claimed")

        incident = self.store.get_incident(approval.incident_id)
        try:
            if incident is None:
                raise PolicyDeniedError("incident no longer exists")
            if self.policy.is_no_retry_dag(approval.dag_id):
                raise PolicyDeniedError(f"DAG {approval.dag_id} is configured as no-retry")
            if approval.requested_action is ProposedAction.RERUN_DAG:
                result = self._execute_rerun(incident, approval)
            else:
                result = self._execute_mark_success(incident, approval)
        except (OncallError, AssertionError) as exc:
            self.store.complete_approval(approval_id, succeeded=False, result=str(exc)[:500])
            self.audit.record(AuditEventType.ACTION_FAILED, actor="agent", action=f"execute_{approval.requested_action.value}",
                              incident_id=approval.incident_id, output_summary=str(exc), success=False,
                              details={"approval_id": approval_id, "approver": approval.approver})  # fmt: skip
            if incident is not None:
                self.escalate(incident, [f"approved {approval.requested_action.value} could not be executed: {exc}"])
            return RecoveryResult("failed", str(exc))

        self.store.complete_approval(approval_id, succeeded=True, result=result.detail[:500])
        self.audit.record(AuditEventType.ACTION_EXECUTED, actor="agent", action=f"execute_{approval.requested_action.value}",
                          incident_id=approval.incident_id, output_summary=result.detail,
                          details={"approval_id": approval_id, "approver": approval.approver, "source": approval.approval_source.value if approval.approval_source else None,
                                   "cleared": result.cleared})  # fmt: skip
        return result

    def _execute_rerun(self, incident: Incident, approval: ApprovalRecord) -> RecoveryResult:
        """Decision A1: clear only failed/upstream_failed instances of that run."""
        failed = self.airflow.list_task_instances(approval.dag_id, approval.dag_run_id, states=sorted(CLEARABLE_STATES))
        ids = sorted({t.task_id for t in failed if t.dag_run_id == approval.dag_run_id})
        if not ids:
            raise PolicyDeniedError("the DAG run has no failed or upstream_failed task instances to clear (decision A1 never clears successful tasks)")
        plan = self._safe_clear_plan(approval.dag_id, approval.dag_run_id, ids, target_ids=set(ids), include_downstream=False, mode="run")
        verify_task = incident.task_id or DAG_RUN_TASK_ID
        original_try = None
        if incident.task_id:
            original_try = next((t.try_number for t in failed if t.task_id == incident.task_id), None)
        retry_id = self.store.start_retry_attempt(
            incident_id=incident.id, dag_id=approval.dag_id, dag_run_id=approval.dag_run_id, task_id=verify_task, map_index=-1,  # type: ignore[arg-type]
            trigger_type="approved", idempotency_key=f"approval:{approval.id}", original_try_number=original_try,
        )  # fmt: skip
        result = self._clear(incident, retry_id, approval.dag_id, approval.dag_run_id, plan, trigger="approved")
        if result.status != "cleared":
            raise PolicyDeniedError(result.detail)
        return RecoveryResult("executed", f"rerun_dag: cleared {', '.join(plan)} in run {approval.dag_run_id}", retry_id, plan)

    def _execute_mark_success(self, incident: Incident, approval: ApprovalRecord) -> RecoveryResult:
        if not approval.task_id:
            raise PolicyDeniedError("mark_success needs a task id")
        target = self.airflow.get_task_instance(approval.dag_id, approval.dag_run_id, approval.task_id)
        if target.state != "failed":
            raise UnsafeClearError(f"task {approval.task_id} is '{target.state}', not failed; not marked success")
        preview = self.airflow.set_task_instance_state(approval.dag_id, approval.dag_run_id, approval.task_id, "success", map_index=target.map_index, dry_run=True)
        if [(t.dag_run_id, t.task_id) for t in preview] != [(approval.dag_run_id, approval.task_id)]:
            raise UnsafeClearError(f"mark_success would affect {[t.task_id for t in preview]}, expected only {approval.task_id}")
        self.airflow.set_task_instance_state(approval.dag_id, approval.dag_run_id, approval.task_id, "success", map_index=target.map_index, dry_run=False)
        self.resolve(incident, f"task {approval.task_id} marked success after approval by {approval.approver}")
        return RecoveryResult("executed", f"mark_success: {approval.task_id} in run {approval.dag_run_id}")

    # ================================================================= clearing (FR-057..059)

    def _safe_clear_plan(
        self, dag_id: str, run_id: str, task_ids: list[str], *, target_ids: set[str], include_downstream: bool, mode: str
    ) -> list[str]:
        """Dry-run the clear and validate every affected task instance. Raises
        :class:`UnsafeClearError` instead of clearing anything questionable."""
        preview = self.airflow.clear_task_instances(dag_id, run_id, task_ids, dry_run=True, only_failed=True, include_downstream=include_downstream)
        if not preview:
            raise UnsafeClearError("dry-run selected no task instances")
        problems: list[str] = []
        for ti in preview:
            where = f"{ti.dag_id}/{ti.dag_run_id}/{ti.task_id}"
            if ti.dag_id != dag_id or ti.dag_run_id != run_id:
                problems.append(f"{where} is outside the target run (FR-059)")
            if ti.state not in CLEARABLE_STATES:
                problems.append(f"{where} is '{ti.state}' (only failed/upstream_failed may be cleared, FR-058)")
            if mode == "task" and ti.task_id not in target_ids and ti.state != "upstream_failed":
                problems.append(f"{where} is not an upstream_failed child of the target (FR-057)")
        missing = target_ids - {ti.task_id for ti in preview}
        if missing:
            problems.append(f"target task(s) {sorted(missing)} not in the dry-run result")
        if mode == "task" and any(ti.task_id in target_ids and ti.state != "failed" for ti in preview):
            problems.append("target task is not in state 'failed'")
        if problems:
            raise UnsafeClearError("; ".join(problems))
        return sorted({ti.task_id for ti in preview})

    def _clear(self, incident: Incident, retry_id: int, dag_id: str, run_id: str, plan: list[str], *, trigger: str) -> RecoveryResult:
        try:
            cleared: list[TaskInstance] = self.airflow.clear_task_instances(
                dag_id, run_id, plan, dry_run=False, only_failed=True, include_downstream=False
            )
        except AirflowError as exc:
            if exc.retriable:
                # Outcome unknown (e.g. timeout). Never repeat the write; verification
                # will see whether the task re-ran, and the timeout escalates otherwise.
                self.store.finish_retry_attempt(retry_id, "verifying", f"clear request outcome unknown: {exc}")
                self._set_status(incident, IncidentStatus.RETRYING, f"{trigger} retry sent (outcome unknown: {exc}); verifying")
                self.audit.record(AuditEventType.RETRY_STARTED, actor="agent", action=f"{trigger}_clear", incident_id=incident.id,
                                  output_summary=f"clear outcome unknown ({exc}); verifying", success=False, details={"retry_id": retry_id, "planned": plan})  # fmt: skip
                return RecoveryResult("cleared", f"clear sent, outcome unknown: {exc}", retry_id, plan)
            self.store.finish_retry_attempt(retry_id, "error", str(exc)[:500])
            self.escalate(incident, [f"Airflow rejected the clear request: {exc}"])
            return RecoveryResult("error", str(exc), retry_id)

        unexpected = sorted({t.task_id for t in cleared} - set(plan))
        if unexpected:  # cannot happen with explicit ids + only_failed, but never stay silent
            log.error("Airflow cleared unexpected task instances %s for incident %s", unexpected, incident.id)
        self.store.finish_retry_attempt(retry_id, "verifying", f"cleared {', '.join(plan)}")
        self._set_status(incident, IncidentStatus.RETRYING, f"{trigger} retry: cleared {', '.join(plan)} in run {run_id}")
        self.audit.record(
            AuditEventType.RETRY_STARTED, actor="agent", action=f"{trigger}_clear", incident_id=incident.id,
            input_summary={"dag_id": dag_id, "dag_run_id": run_id, "task_ids": plan},
            output_summary=f"cleared {len(plan)} task instance(s): {', '.join(plan)}",
            success=not unexpected, details={"retry_id": retry_id, "unexpected": unexpected},
        )  # fmt: skip
        return RecoveryResult("cleared", f"cleared {', '.join(plan)}", retry_id, plan)

    # ================================================================= verification (FR-060..064)

    def verify_pending(self) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        timeout = timedelta(minutes=self.settings.retry_verify_timeout_minutes)
        for attempt in self.store.list_retry_attempts(statuses=("verifying",)):
            try:
                results.append(self._verify(attempt, timeout))
            except OncallError:
                log.exception("Verifying retry %s failed", attempt["id"])
        return results

    def _verify(self, attempt: dict, timeout: timedelta) -> VerificationResult:
        retry_id, incident_id = attempt["id"], attempt["incident_id"]
        elapsed = self._clock() - from_db_time(attempt["started_at"])  # type: ignore[operator]
        outcome, detail = "pending", ""
        try:
            if attempt["task_id"] == DAG_RUN_TASK_ID:
                state = self.airflow.get_dag_run(attempt["dag_id"], attempt["dag_run_id"]).state
                if state == "success":
                    outcome, detail = "succeeded", "DAG run succeeded"
                elif state == "failed":
                    outcome, detail = "failed", "DAG run failed again"
                else:
                    detail = f"DAG run is {state}"
            else:
                ti = self.airflow.get_task_instance(attempt["dag_id"], attempt["dag_run_id"], attempt["task_id"], attempt["map_index"])
                original = attempt["original_try_number"]
                advanced = original is None or ti.try_number > original
                if ti.state == "success":
                    outcome, detail = "succeeded", f"task succeeded on try {ti.try_number}"
                elif ti.state in FAILED_AGAIN_STATES and advanced:
                    outcome, detail = "failed", f"task failed again on try {ti.try_number}"
                elif ti.state in FAILED_AGAIN_STATES:
                    detail = f"task still '{ti.state}' on try {ti.try_number}; the clear has not taken effect"
                else:
                    detail = f"task is {ti.state or 'cleared (no state)'} on try {ti.try_number}"
        except AirflowError as exc:
            detail = f"could not read state: {exc}"

        incident = self.store.get_incident(incident_id)
        if outcome == "succeeded":
            self.store.finish_retry_attempt(retry_id, "succeeded", detail)
            self.audit.record(AuditEventType.RETRY_SUCCEEDED, actor="agent", action="verify_retry", incident_id=incident_id,
                              output_summary=detail, details={"retry_id": retry_id})  # fmt: skip
            if incident is not None:
                self.resolve(incident, f"retry succeeded: {detail}")
        elif outcome == "failed":
            self.store.finish_retry_attempt(retry_id, "failed", detail)
            self.audit.record(AuditEventType.RETRY_FAILED, actor="agent", action="verify_retry", incident_id=incident_id,
                              output_summary=detail, success=False, details={"retry_id": retry_id})  # fmt: skip
            if incident is not None:
                self.escalate(incident, [f"retry did not fix the failure: {detail}"])
        elif elapsed >= timeout:
            outcome = "timed_out"
            detail = f"no result after {int(elapsed.total_seconds() // 60)} minutes ({detail})"
            self.store.finish_retry_attempt(retry_id, "timed_out", detail)
            self.audit.record(AuditEventType.RETRY_TIMED_OUT, actor="agent", action="verify_retry", incident_id=incident_id,
                              output_summary=detail, success=False, details={"retry_id": retry_id})  # fmt: skip
            if incident is not None:
                self.escalate(incident, [f"retry still not finished: {detail}"])
        return VerificationResult(retry_id, incident_id, outcome, detail)

    # ================================================================= status helpers

    def escalate(self, incident: Incident, reasons: list[str]) -> RecoveryResult:
        self._set_status(incident, IncidentStatus.ESCALATED, "; ".join(reasons))
        self.audit.record(AuditEventType.INCIDENT_ESCALATED, actor="agent", action="escalate", incident_id=incident.id,
                          output_summary="; ".join(reasons), details={"reasons": reasons})  # fmt: skip
        return RecoveryResult("escalated", "; ".join(reasons))

    def resolve(self, incident: Incident, reason: str) -> None:
        self._set_status(incident, IncidentStatus.RESOLVED, reason)
        open_approval = self.store.get_open_approval(incident.id)  # type: ignore[arg-type]
        if open_approval is not None and open_approval.status is ApprovalStatus.PENDING:
            self.store.reject_approval(open_approval.id, "incident resolved before approval")  # type: ignore[arg-type]
        self.audit.record(AuditEventType.INCIDENT_RESOLVED, actor="agent", action="resolve", incident_id=incident.id, output_summary=reason)

    def _refuse(self, incident: Incident, status: str, detail: str) -> RecoveryResult:
        self.audit.record(AuditEventType.RETRY_BLOCKED, actor="agent", action="auto_retry", incident_id=incident.id,
                          output_summary=detail, success=False)  # fmt: skip
        self.escalate(incident, [detail])
        return RecoveryResult(status, detail)

    def _blocked(self, incident: Incident, detail: str) -> RecoveryResult:
        return self._refuse(incident, "blocked", f"unsafe clear refused: {detail}")

    def _set_status(self, incident: Incident, status: IncidentStatus, reason: str | None = None) -> None:
        current = self._reload(incident)
        if current.status is not status or (reason and reason != current.status_reason):
            self.store.update_incident(current.model_copy(update={"status": status, "status_reason": reason or current.status_reason}))

    def _reload(self, incident: Incident) -> Incident:
        return (self.store.get_incident(incident.id) if incident.id is not None else None) or incident

    def _global_count(self) -> int | None:
        try:
            return self.store.count_automatic_retries_since(self._clock() - timedelta(hours=1))
        except OncallError:
            return None
