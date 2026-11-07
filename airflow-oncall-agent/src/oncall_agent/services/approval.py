"""Human approval of restricted actions via Linear (FR-066..FR-073, NFR-012; decision A2).

For every *pending* approval request whose incident has a Linear issue, each cycle:

1. Collect approval evidence that appeared **after** the request:
   * the label ``oncall:approved`` being added (actor taken from the issue history), or
   * a comment whose first line is exactly ``/approve`` (actor = comment author).
2. Validate each candidate, oldest first (FR-071, NFR-012). It is rejected if:
   * the approver's identity is unknown;
   * the approver is the agent's own Linear user (e.g. a label added by the agent or
     through the Linear MCP server) — an MCP call or AI output can never approve;
   * the approver is not in ``ONCALL_TRUSTED_APPROVERS`` (Linear user ID or email).
     An **empty allowlist rejects everyone**, except in mock mode (development);
   * the incident is no longer waiting for approval.
   Rejections are audited and explained once on the issue.
3. The first valid candidate approves the request (source, approver, time — FR-072) and
   the action is executed through ``RecoveryService.execute_approved``, which claims the
   approval exactly once (FR-070). Later approvals of the same request change nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..config import Settings
from ..exceptions import OncallError
from ..integrations.linear import LinearClient, LinearUser
from ..models import ApprovalRecord, ApprovalSource, ApprovalStatus, AuditEventType, IncidentStatus
from ..reporting import approval_rejected_comment, approval_result_comment
from ..store import Store
from .audit import AuditService
from .recovery import RecoveryService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    key: str
    source: ApprovalSource
    actor: LinearUser | None
    at: datetime

    @property
    def who(self) -> str:
        if self.actor is None:
            return "an unknown user"
        return self.actor.email or self.actor.name or self.actor.id


@dataclass
class ApprovalOutcome:
    approval_id: int
    status: str  # executed | failed | rejected | waiting | skipped
    detail: str


class ApprovalService:
    def __init__(self, linear: LinearClient, store: Store, audit: AuditService, recovery: RecoveryService, settings: Settings) -> None:
        self.linear = linear
        self.store = store
        self.audit = audit
        self.recovery = recovery
        self.settings = settings
        self._viewer: LinearUser | None = None
        self._rejected: set[str] = set()  # candidate keys already reported (per process)

    @property
    def trusted(self) -> set[str]:
        return {a.strip().lower() for a in self.settings.approval.trusted_approvers if a.strip()}

    def check_pending(self) -> list[ApprovalOutcome]:
        pending = self.store.list_approvals(statuses=[ApprovalStatus.PENDING])
        if not pending:
            return []
        if self._viewer is None:
            self._viewer = self.linear.viewer()
        label_id = self.linear.find_label_ids([self.settings.linear.labels.approved]).get(self.settings.linear.labels.approved)
        outcomes: list[ApprovalOutcome] = []
        for approval in pending:
            try:
                outcomes.append(self._check(approval, label_id))
            except OncallError as exc:
                log.warning("Approval %s could not be checked: %s", approval.id, exc)
                self.audit.record(AuditEventType.INTEGRATION_FAILURE, actor="agent", action="check_approval", incident_id=approval.incident_id,
                                  output_summary=str(exc), success=False, details={"approval_id": approval.id})  # fmt: skip
                outcomes.append(ApprovalOutcome(approval.id or -1, "waiting", f"could not check: {exc}"))
        return outcomes

    # ----------------------------------------------------------------- one request

    def _check(self, approval: ApprovalRecord, label_id: str | None) -> ApprovalOutcome:
        incident = self.store.get_incident(approval.incident_id)
        if incident is None or not incident.linear_issue_id:
            return ApprovalOutcome(approval.id or -1, "waiting", "no Linear issue yet")
        for cand in self._candidates(incident.linear_issue_id, approval, label_id):
            problem = self._problem(cand, incident.status)
            if problem:
                self._reject(approval, incident.linear_issue_id, cand, problem)
                continue
            return self._approve(approval, incident.linear_issue_id, cand)
        return ApprovalOutcome(approval.id or -1, "waiting", "no valid approval yet")

    def _candidates(self, issue_id: str, approval: ApprovalRecord, label_id: str | None) -> list[Candidate]:
        found: list[Candidate] = []
        if label_id:
            for e in self.linear.list_label_events(issue_id):
                if label_id in e.added_label_ids and e.created_at > approval.requested_at:
                    found.append(Candidate(f"label:{issue_id}:{e.created_at.isoformat()}:{e.actor.id if e.actor else '?'}", ApprovalSource.LINEAR_LABEL, e.actor, e.created_at))
        command = self.settings.approval.comment_command.strip().lower()
        for c in self.linear.list_comments(issue_id):
            first = next((line.strip().lower() for line in c.body.splitlines() if line.strip()), "")
            if first == command and c.created_at > approval.requested_at:
                found.append(Candidate(f"comment:{c.id}", ApprovalSource.LINEAR_COMMENT, c.user, c.created_at))
        return sorted(found, key=lambda c: c.at)

    def _problem(self, cand: Candidate, status: IncidentStatus) -> str | None:
        """Why a candidate is not a valid approval, or None (FR-071, NFR-012)."""
        if cand.actor is None or not cand.actor.id:
            return "the approver's identity is unknown"
        if self._viewer is not None and cand.actor.id == self._viewer.id:
            return "approvals made by the agent's own Linear user are not accepted"
        trusted = self.trusted
        if not trusted:
            if not self.settings.use_mocks:
                return "no trusted approvers are configured (ONCALL_TRUSTED_APPROVERS is empty)"
        elif cand.actor.id.lower() not in trusted and (cand.actor.email or "").lower() not in trusted:
            return f"{cand.who} is not a trusted approver"
        if status is not IncidentStatus.AWAITING_APPROVAL:
            return f"the incident is {status.value}, not waiting for approval"
        return None

    def _reject(self, approval: ApprovalRecord, issue_id: str, cand: Candidate, reason: str) -> None:
        if cand.key in self._rejected:
            return
        self._rejected.add(cand.key)
        self.audit.record(
            AuditEventType.APPROVAL_REJECTED, actor=f"linear:{cand.who}", action=f"approve_{approval.requested_action.value}",
            incident_id=approval.incident_id, output_summary=reason, success=False,
            details={"approval_id": approval.id, "source": cand.source.value, "approver_id": cand.actor.id if cand.actor else None},
        )  # fmt: skip
        try:
            self.linear.add_comment(issue_id, approval_rejected_comment(cand.who, reason))
        except OncallError:
            log.warning("Could not post approval rejection to %s", issue_id)

    def _approve(self, approval: ApprovalRecord, issue_id: str, cand: Candidate) -> ApprovalOutcome:
        assert approval.id is not None and cand.actor is not None
        approver = cand.actor.email or cand.actor.id
        if not self.store.record_approval(approval.id, source=cand.source, approver=approver, approved_at=cand.at):
            return ApprovalOutcome(approval.id, "skipped", "approval was already recorded")
        self.audit.record(
            AuditEventType.APPROVAL_RECEIVED, actor=f"linear:{approver}", action=f"approve_{approval.requested_action.value}",
            incident_id=approval.incident_id, output_summary=f"approved via {cand.source.value} by {approver}",
            details={"approval_id": approval.id, "source": cand.source.value, "approver_id": cand.actor.id, "approved_at": cand.at.isoformat()},
        )  # fmt: skip
        result = self.recovery.execute_approved(approval.id)
        status = "executed" if result.status == "executed" else "failed"
        try:
            self.linear.add_comment(issue_id, approval_result_comment(approval.requested_action.value, approver, result.detail, status == "executed"))
        except OncallError:
            log.warning("Could not post approval result to %s", issue_id)
        return ApprovalOutcome(approval.id, status, result.detail)
