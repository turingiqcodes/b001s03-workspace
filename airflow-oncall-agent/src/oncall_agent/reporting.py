"""Human-readable incident reports for Linear and the CLI (FR-074..077, FR-029).

Every function returns Markdown built only from the stored incident, so reports can be
(re)generated after a restart. All text is passed through :func:`redact` because task
logs and error lines can contain credentials (NFR-009).

Linear conventions: short sentences, the same terms throughout, and agent-specific
detail (fingerprint inputs, policy checks) in a collapsible "Agent notes" section.
The line ``oncall-fingerprint: <sha256>`` in every description lets the agent find its
own issue again if its database is lost (FR-018).
"""
from __future__ import annotations

from typing import Any

from .logging_config import redact
from .models import FailureType, Incident, IncidentStatus, ProposedAction

FINGERPRINT_MARKER = "oncall-fingerprint:"
MAX_TITLE = 120


def fingerprint_marker(fingerprint: str) -> str:
    return f"{FINGERPRINT_MARKER} {fingerprint}"


def _target(incident: Incident) -> str:
    if incident.failure_type is FailureType.IMPORT_ERROR:
        return f"import error in {incident.dag_id}"
    if incident.failure_type is FailureType.DAG_RUN_FAILURE:
        return f"DAG run of {incident.dag_id} failed"
    return f"{incident.dag_id}.{incident.task_id} failed"


def issue_title(incident: Incident) -> str:
    title = f"[oncall] {_target(incident)}: {incident.normalized_error}"
    return redact(title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1] + "…")


def _analysis_lines(incident: Incident) -> list[str]:
    if incident.classification is None:
        return ["Analysis is not available yet."]
    source = "AI analysis" if incident.analysis_source and incident.analysis_source.value == "ai" else "Rule-based fallback (AI unavailable)"
    lines = [
        f"- **Classification:** {incident.classification.value} (confidence {incident.confidence:.2f}, {source})",
        f"- **Proposed action:** {incident.proposed_action.value if incident.proposed_action else 'none'}",
        f"- **Root cause:** {incident.root_cause}",
        f"- **Suggested fix:** {incident.suggested_fix}",
    ]
    if incident.suspected_commit:
        lines.append(f"- **Suspected commit:** `{incident.suspected_commit[:7]}` (not confirmed)")
    if incident.analysis_fallback_reason:
        lines.append(f"- **Why the fallback was used:** {incident.analysis_fallback_reason}")
    return lines


def issue_description(incident: Incident, *, approval_label: str, approve_command: str, ownership_note: str | None = None) -> str:
    s: dict[str, Any] = incident.context_summary or {}
    parts = [
        f"**{_target(incident)}.** {incident.status_reason or ''}".strip(),
        "",
        "## Failure",
        f"- **DAG:** `{incident.dag_id}`" + (f" · **Task:** `{incident.task_id}`" if incident.task_id else ""),
        f"- **Run:** `{incident.dag_run_id}`" if incident.dag_run_id else "- **Run:** none (import error)",
        f"- **Error:** `{incident.original_error or incident.normalized_error}`",
        f"- **First seen:** {incident.first_detected_at:%Y-%m-%d %H:%M UTC} · **Occurrences:** {incident.occurrence_count}",
        f"- **Status:** {incident.status.value}",
        "",
        "## Analysis",
        *_analysis_lines(incident),
    ]
    if incident.status is IncidentStatus.AWAITING_APPROVAL and incident.proposed_action in (ProposedAction.RERUN_DAG, ProposedAction.MARK_SUCCESS):
        parts += [
            "",
            "## Approval needed",
            f"The agent proposes **{incident.proposed_action.value}**. It runs only after a trusted approver adds the "
            f"label `{approval_label}` or comments `{approve_command}`. It runs once.",
        ]
    parts += ["", "+++ Agent notes", "", fingerprint_marker(incident.fingerprint), ""]
    parts.append(f"Fingerprint inputs: `{incident.fingerprint_input.get('format', '')}` → `{incident.normalized_error}`")
    if ownership_note:
        parts.append(f"Ownership: {ownership_note}")
    if s.get("commit_search"):
        parts.append(f"Commit search: {s['commit_search']}")
    parts += ["", "+++"]
    return redact("\n".join(parts))


def diagnostics_comment(incident: Incident) -> str:
    """Investigation summary posted once when the issue is created (FR-077)."""
    s: dict[str, Any] = incident.context_summary or {}
    parts = ["**Investigation summary**", ""]
    if incident.evidence:
        parts += ["Evidence:", *[f"- {e}" for e in incident.evidence], ""]
    # FR-029: missing evidence is listed separately and never presented as a cause.
    if incident.missing_evidence:
        parts += ["Missing evidence (could not be collected, so it is not proof of anything):", *[f"- {m}" for m in incident.missing_evidence], ""]
    if s.get("history"):
        parts += [f"Run history: {s['history']}.", ""]
    related = [c for c in s.get("commits", []) if c.get("possibly_related")]
    if related:
        parts.append("Possibly related changes (heuristic, not confirmed):")
        parts += [f"- `{c['sha'][:7]}` {c.get('title', '')} by {c.get('author_name') or c.get('author_login') or 'unknown'}: {'; '.join(c.get('relation_reasons', []))}" for c in related]
        parts.append("")
    elif s.get("commit_search"):
        parts += [f"No commit was linked to this failure ({s['commit_search']}). That is not proof that nothing changed.", ""]
    if incident.analysis_explanation:
        parts += [f"Analysis explanation: {incident.analysis_explanation}", ""]
    if s.get("log_tail"):
        parts += ["Log tail:", "```", s["log_tail"], "```"]
    return redact("\n".join(parts).strip())


STATUS_HEADLINES = {
    IncidentStatus.OPEN: "Incident is open",
    IncidentStatus.INVESTIGATING: "Investigation in progress",
    IncidentStatus.RETRYING: "Automatic retry started",
    IncidentStatus.AWAITING_APPROVAL: "Waiting for approval",
    IncidentStatus.ESCALATED: "Escalated to a human",
    IncidentStatus.RESOLVED: "Resolved",
}


def status_comment(incident: Incident) -> str:
    text = f"**{STATUS_HEADLINES[incident.status]}.**"
    if incident.status_reason:
        text += f" {incident.status_reason}"
    return redact(text)


def occurrence_comment(incident: Incident) -> str:
    return redact(
        f"The failure occurred again ({incident.occurrence_count} occurrences in total). "
        f"Latest run: `{incident.dag_run_id}` at {incident.last_detected_at:%Y-%m-%d %H:%M UTC}."
    )


def approval_rejected_comment(who: str, reason: str) -> str:
    return redact(f"An approval from **{who}** was ignored: {reason}. The proposed action has not run.")


def approval_result_comment(action: str, who: str, outcome: str, succeeded: bool) -> str:
    verb = "was executed" if succeeded else "could not be executed"
    return redact(f"**{action}** approved by {who} {verb}: {outcome}.")
