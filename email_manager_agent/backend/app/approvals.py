"""Human approval for write operations.

Lifecycle of a write:
  1. Claude calls a write tool.  -> propose(): validate, build a Proposal, store a PendingOperation.
  2. The frontend shows it.       -> the user clicks Approve or Reject.
  3. POST /api/confirm            -> decide(): look up the operation by session + id, check it is
                                     still pending and not expired, then run the stored parameters.

The frontend only sends an operation id and a decision. What runs is always the
parameters stored at step 1, so an approval cannot be widened or swapped.
"""
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Literal

from app.agent.tools import TOOLS_BY_NAME, ToolSpec
from app.errors import ApprovalError, AppError, BridgeTimeout, NotFound, ValidationFailed
from app.services.common import Proposal, ServiceContext
from app.sessions import PendingOperation, Session

logger = logging.getLogger(__name__)

MAX_OPEN_OPERATIONS = 5


class ApprovalExpired(ApprovalError):
    code = "approval_expired"
    http_status = 410


class AcknowledgementRequired(ApprovalError):
    code = "acknowledgement_required"
    http_status = 409


def _fingerprint(tool_name: str, params: dict[str, Any]) -> str:
    return tool_name + ":" + json.dumps(params, sort_keys=True, default=str)


def store_proposal(
    session: Session, spec: ToolSpec, proposal: Proposal, ctx: ServiceContext
) -> tuple[PendingOperation, bool]:
    """Save a validated proposal as a pending operation. Returns (operation, newly_created).

    If an identical operation is already waiting, that one is returned instead of a duplicate.
    """
    now = ctx.now()
    session.expire_operations(now)
    fingerprint = _fingerprint(spec.name, proposal.execute_params)

    for op in session.open_operations():
        if _fingerprint(op.tool_name, op.execute_params) == fingerprint:
            return op, False  # same request twice: reuse it instead of queueing a duplicate write

    if len(session.open_operations()) >= MAX_OPEN_OPERATIONS:
        raise ValidationFailed(
            f"There are already {MAX_OPEN_OPERATIONS} operations waiting for approval. "
            "Ask the user to approve or reject those first."
        )

    warnings = list(proposal.warnings)
    already_done = [
        op for op in session.operations.values()
        if op.status == "executed" and _fingerprint(op.tool_name, op.execute_params) == fingerprint
    ]
    if already_done:
        warnings.append("An identical operation was already carried out in this session. Approving repeats it.")

    conflicts = proposal.details.get("conflicts") or []
    op = PendingOperation(
        id="op_" + secrets.token_urlsafe(12),
        tool_name=spec.name,
        summary=proposal.summary,
        details=proposal.details,
        warnings=warnings,
        conflict_event_ids=[c["event_id"] for c in conflicts if isinstance(c, dict) and "event_id" in c],
        execute_params=proposal.execute_params,
        created_at=now,
        expires_at=now + timedelta(seconds=ctx.settings.approval_ttl_seconds),
    )
    session.operations[op.id] = op
    logger.info("operation proposed: %s %s", op.id, op.tool_name)
    return op, True


def decide(
    session: Session,
    operation_id: str,
    decision: Literal["approve", "reject"],
    acknowledge_warnings: bool,
    ctx: ServiceContext,
) -> PendingOperation:
    """Apply the user's decision to one pending operation. Caller must hold the session lock."""
    op = session.operations.get(operation_id)
    if op is None:
        raise NotFound("No operation with this id exists in this session.")

    session.expire_operations(ctx.now())
    if op.status == "expired":
        raise ApprovalExpired("This approval request expired. Nothing was done; ask again if you still want it.")
    if op.status != "pending":
        raise ApprovalError(f"This operation is already {op.status}; it cannot be decided again.")

    if decision == "reject":
        op.status = "rejected"
        session.pending_notes.append(f"The user REJECTED operation {op.id} ({op.tool_name}: {op.summary}). It was not done.")
        logger.info("operation rejected: %s", op.id)
        return op

    if op.warnings and not acknowledge_warnings:
        raise AcknowledgementRequired(
            "This operation has warnings (for example a calendar conflict). Review them and approve again "
            "with the warnings acknowledged."
        )

    spec = TOOLS_BY_NAME.get(op.tool_name)
    if spec is None or spec.kind != "write" or spec.execute is None:
        op.status = "failed"
        raise ApprovalError("This operation type cannot be executed.")

    # Mark it before running so a second request can never execute it again.
    op.status = "executing"
    try:
        op.result = spec.execute(ctx, op.execute_params, op.conflict_event_ids)
        op.status = "executed"
        session.pending_notes.append(
            f"The user APPROVED operation {op.id} ({op.tool_name}: {op.summary}) and it was carried out. "
            f"Result: {json.dumps(op.result, default=str)[:2000]}"
        )
        logger.info("operation executed: %s", op.id)
    except AppError as exc:
        op.status = "failed"
        op.error = exc.message
        if isinstance(exc, BridgeTimeout):
            # The write may still have happened on Google's side; never retry it automatically.
            op.error += " It may still have gone through: check Google before trying again."
        session.pending_notes.append(
            f"The user approved operation {op.id} ({op.tool_name}: {op.summary}) but it FAILED: {op.error}"
        )
        logger.warning("operation failed: %s (%s)", op.id, exc.code)
    except Exception:  # noqa: BLE001 - report any unexpected failure without leaking details
        op.status = "failed"
        op.error = "Unexpected error while carrying out the operation."
        session.pending_notes.append(f"Operation {op.id} ({op.tool_name}) FAILED with an unexpected error.")
        logger.exception("operation crashed: %s", op.id)
    return op
