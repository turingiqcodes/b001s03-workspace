"""Deterministic policy engine (FR-041, FR-045, FR-047..FR-055, FR-066/067/073; SRS Part 10).

The policy engine is the only component that decides what the agent may do. It is a
pure function of the incident (with its stored analysis), the retry counters and the
configuration — no AI, no I/O — so every decision is reproducible and testable.

Automatic retry is allowed only if EVERY check passes (FR-050, plus fail-closed extras):

  1. failure is a task failure                         (FR-050)
  2. classification is ``retriable``                    (FR-050)
  3. confidence ≥ 0.7                                   (FR-050; fixed by the SRS, not configurable)
  4. proposed action is ``retry_task``                  (FR-050)
  5. incident retries this cycle < ONCALL_MAX_AUTO_RETRIES        (FR-050, A3)
  6. automatic retries in the last hour < ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR (FR-050)
  7. the DAG is not in ``policy.no_retry_dags``          (FR-050, A4)
  8. automatic retries are not disabled (both limits > 0)          (FR-050/051)
  9. the analysis came from the AI provider, not the fallback      (FR-045)
 10. the incident is in a state that can be retried (open/investigating) — prevents a
     second retry while one is running, awaiting approval, escalated or resolved

Any missing or unknown value fails its check (FR-054). Every check is reported, so the
audit trail shows exactly why a retry was or was not allowed (FR-053).

Outcomes:

* ``auto_retry``        — all checks passed.
* ``request_approval``  — the AI proposed a restricted action (``rerun_dag``/``mark_success``)
                          that is structurally valid for this incident; a human must approve it
                          (FR-055/066/067). The AI proposal alone never executes it (FR-073).
* ``escalate``          — anything else that needs a human, including a ``retry_task`` that
                          failed a check (decision A2: not approvable in v1).
* ``no_action``         — the analysis proposed ``no_action``.

The policy decision is re-checked by the recovery service immediately before any Airflow
write, and the retry limits are re-checked atomically when the retry is reserved
(``Store.start_retry_attempt``), so a decision cannot go stale between evaluation and
execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .config import PolicySettings
from .models import (
    RESTRICTED_ACTIONS,
    AnalysisSource,
    Classification,
    FailureType,
    Incident,
    IncidentStatus,
    ProposedAction,
)

MIN_AUTO_RETRY_CONFIDENCE = 0.7  # FR-050. Deliberately a constant, not configuration.
RETRYABLE_STATUSES = frozenset({IncidentStatus.OPEN, IncidentStatus.INVESTIGATING})


class Decision(str, Enum):
    AUTO_RETRY = "auto_retry"
    REQUEST_APPROVAL = "request_approval"
    ESCALATE = "escalate"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    action: ProposedAction | None
    checks: tuple[Check, ...]
    reasons: tuple[str, ...]
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def auto_retry_allowed(self) -> bool:
        return self.decision is Decision.AUTO_RETRY

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_audit(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "action": self.action.value if self.action else None,
            "reasons": list(self.reasons),
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }


class PolicyEngine:
    def __init__(self, settings: PolicySettings) -> None:
        self.settings = settings

    @property
    def auto_retries_enabled(self) -> bool:
        return self.settings.max_auto_retries > 0 and self.settings.global_max_retries_per_hour > 0

    def is_no_retry_dag(self, dag_id: str) -> bool:
        return dag_id in self.settings.no_retry_dags

    # ----------------------------------------------------------------- automatic retry checks

    def retry_checks(self, incident: Incident, global_retries_last_hour: int | None) -> tuple[Check, ...]:
        s = self.settings
        conf = incident.confidence
        return (
            Check("task_failure", incident.failure_type is FailureType.TASK_FAILURE and bool(incident.task_id) and bool(incident.dag_run_id),
                  f"failure type is {incident.failure_type.value}"),
            Check("classification_retriable", incident.classification is Classification.RETRIABLE,
                  f"classification is {incident.classification.value if incident.classification else 'missing'}"),
            Check("confidence_threshold", conf is not None and conf >= MIN_AUTO_RETRY_CONFIDENCE,
                  f"confidence {conf if conf is not None else 'missing'} (needs ≥ {MIN_AUTO_RETRY_CONFIDENCE})"),
            Check("proposed_retry_task", incident.proposed_action is ProposedAction.RETRY_TASK,
                  f"proposed action is {incident.proposed_action.value if incident.proposed_action else 'missing'}"),
            Check("incident_retry_limit", incident.cycle_retry_count < s.max_auto_retries,
                  f"{incident.cycle_retry_count} automatic retries this cycle (limit {s.max_auto_retries})"),
            Check("global_retry_limit", global_retries_last_hour is not None and global_retries_last_hour < s.global_max_retries_per_hour,
                  f"{global_retries_last_hour if global_retries_last_hour is not None else 'unknown'} automatic retries in the last hour (limit {s.global_max_retries_per_hour})"),
            Check("dag_not_no_retry", not self.is_no_retry_dag(incident.dag_id),
                  f"DAG {incident.dag_id} is {'configured as no-retry' if self.is_no_retry_dag(incident.dag_id) else 'not in the no-retry list'}"),
            Check("auto_retries_enabled", self.auto_retries_enabled,
                  "automatic retries are " + ("enabled" if self.auto_retries_enabled else "disabled (a retry limit is 0)")),
            Check("analysis_from_ai", incident.analysis_source is AnalysisSource.AI,
                  f"analysis source is {incident.analysis_source.value if incident.analysis_source else 'missing'} (fallback can never authorize a retry)"),
            Check("incident_state", incident.status in RETRYABLE_STATUSES,
                  f"incident status is {incident.status.value}"),
        )  # fmt: skip

    # ----------------------------------------------------------------- decision

    def evaluate(self, incident: Incident, global_retries_last_hour: int | None) -> PolicyDecision:
        checks = self.retry_checks(incident, global_retries_last_hour)
        action = incident.proposed_action

        if all(c.passed for c in checks):
            return PolicyDecision(Decision.AUTO_RETRY, ProposedAction.RETRY_TASK, checks, ("all automatic retry conditions are satisfied",))

        failed = [c.detail for c in checks if not c.passed]

        if action in RESTRICTED_ACTIONS:
            problem = self._restricted_action_problem(incident, action)  # type: ignore[arg-type]
            if problem is None:
                return PolicyDecision(
                    Decision.REQUEST_APPROVAL, action, checks,
                    (f"{action.value} is a restricted operation and requires human approval",),  # type: ignore[union-attr]
                )  # fmt: skip
            return PolicyDecision(Decision.ESCALATE, action, checks, (problem,))

        if action is ProposedAction.NO_ACTION and incident.analysis_source is AnalysisSource.AI:
            return PolicyDecision(Decision.NO_ACTION, action, checks, ("the analysis proposed no action",))

        if action is ProposedAction.RETRY_TASK:
            reasons = tuple(f"automatic retry refused: {d}" for d in failed)
        else:
            reasons = (f"proposed action is {action.value if action else 'missing'}; a human must investigate",)
        return PolicyDecision(Decision.ESCALATE, action, checks, reasons)

    def _restricted_action_problem(self, incident: Incident, action: ProposedAction) -> str | None:
        """Why a restricted action cannot even be offered for approval, or None."""
        if incident.analysis_source is not AnalysisSource.AI:
            return "restricted actions are never requested from a fallback analysis"
        if self.is_no_retry_dag(incident.dag_id):
            return f"DAG {incident.dag_id} is configured as no-retry; {action.value} is not offered"
        if incident.status not in RETRYABLE_STATUSES:
            return f"incident status is {incident.status.value}; no new action can be requested"
        if not incident.dag_run_id or incident.failure_type is FailureType.IMPORT_ERROR:
            return f"{action.value} needs a DAG run; this failure has none"
        if action is ProposedAction.MARK_SUCCESS and (incident.failure_type is not FailureType.TASK_FAILURE or not incident.task_id):
            return "mark_success needs a failed task instance"
        return None
