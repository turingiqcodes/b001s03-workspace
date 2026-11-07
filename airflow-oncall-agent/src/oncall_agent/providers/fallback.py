"""Rule-based fallback analysis (FR-042..FR-046).

Used when the AI provider is unavailable, times out, or returns an invalid answer.

Safety properties:

* confidence never exceeds 0.5 (``FALLBACK_MAX_CONFIDENCE``, also enforced by the
  ``AnalysisResult`` model);
* the proposed action is always ``manual_investigation`` — the fallback may say a
  failure *looks* transient, but it never proposes a retry. The policy engine
  additionally refuses automatic retries for any fallback analysis (FR-045);
* the result is tagged ``source=fallback`` with the reason the AI was not used.

Patterns are checked against the error line first, then the end of the task log.
The first matching rule wins; order goes from most to least specific.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..context import InvestigationContext
from ..models import (
    FALLBACK_MAX_CONFIDENCE,
    AIAnalysisPayload,
    AnalysisResult,
    AnalysisSource,
    Classification,
    FailureType,
    ProposedAction,
)

LOG_TAIL_LINES = 60


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    classification: Classification
    confidence: float
    root_cause: str
    suggested_fix: str


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


RULES: list[Rule] = [
    Rule("import_or_syntax_error", _r(r"\b(SyntaxError|IndentationError|ModuleNotFoundError|ImportError)\b"),
         Classification.NON_RETRIABLE, 0.5, "The DAG or task code cannot be imported (syntax or missing module).",
         "Fix the code or install the missing dependency, then redeploy. Retrying will fail the same way."),
    Rule("code_error", _r(r"\b(KeyError|AttributeError|TypeError|NameError|IndexError|ZeroDivisionError|AssertionError|UnboundLocalError|NotImplementedError)\b"),
         Classification.NON_RETRIABLE, 0.45, "A programming or data-shape error was raised by the task code.",
         "Inspect the failing line and recent code or upstream schema changes. Retrying is unlikely to help."),
    Rule("auth_or_permission", _r(r"\b(401|403)\b.*\b(Unauthorized|Forbidden)\b|\b(PermissionError|AccessDenied|Access Denied|authentication failed|invalid credentials|permission denied)\b"),
         Classification.NON_RETRIABLE, 0.45, "Credentials or permissions were rejected.",
         "Check the connection credentials and permissions used by the task."),
    Rule("db_lock_or_deadlock", _r(r"\b(LockNotAvailable|deadlock detected|database is locked|could not serialize access|lock timeout|Lock wait timeout)\b"),
         Classification.RETRIABLE, 0.45, "The task lost a database lock race or hit a lock timeout.",
         "Usually transient; confirm no long-running job holds the lock before retrying."),
    Rule("transient_network", _r(r"\b(ReadTimeout|ConnectTimeout|ConnectionError|ConnectionResetError|ConnectionRefusedError|RemoteDisconnected|Read timed out|Connection reset|Temporary failure in name resolution|Name or service not known|ProtocolError)\b"),
         Classification.RETRIABLE, 0.5, "A network call to an external service failed or timed out.",
         "Usually transient; check the upstream service status, then retry the task."),
    Rule("upstream_unavailable", _r(r"\b(502|503|504|429)\b|Service Unavailable|Bad Gateway|Gateway Time-?out|Too Many Requests|rate limit"),
         Classification.RETRIABLE, 0.45, "An upstream service was unavailable or rate limiting.",
         "Usually transient; check the upstream service and rate limits, then retry the task."),
    Rule("killed_or_oom", _r(r"return code -9|\bSIGKILL\b|\bOOMKilled\b|\bMemoryError\b|Killed\b|out of memory"),
         Classification.UNCERTAIN, 0.35, "The task process was killed, possibly for exceeding its memory limit.",
         "Check worker memory metrics and recent data-volume changes before retrying."),
    Rule("task_timeout", _r(r"\bAirflowTaskTimeout\b|execution_timeout|Timeout, PID"),
         Classification.UNCERTAIN, 0.3, "The task exceeded its execution timeout.",
         "Check whether the input volume grew or a dependency became slow."),
    Rule("missing_input", _r(r"\b(FileNotFoundError|NoSuchKey|NoSuchBucket|does not exist|No such file or directory)\b"),
         Classification.UNCERTAIN, 0.35, "An expected file, object or table was missing.",
         "Check whether the upstream producer ran and the path is correct."),
]

DAG_RUN_RULE = Rule("dag_run_failure", _r(r"$^"), Classification.UNCERTAIN, 0.3,
                    "The DAG run failed without a failed task (for example a dagrun_timeout).",
                    "Check the DAG run timeout and scheduler/worker health for that run.")  # fmt: skip
UNKNOWN_RULE = Rule("unknown", _r(r"$^"), Classification.UNCERTAIN, 0.2,
                    "No known failure pattern matched.", "Investigate the task log manually.")  # fmt: skip

assert all(r.confidence <= FALLBACK_MAX_CONFIDENCE for r in [*RULES, DAG_RUN_RULE, UNKNOWN_RULE])


class FallbackAnalyzer:
    name = "rules"

    def analyze(self, ctx: InvestigationContext, reason: str) -> AnalysisResult:
        rule, matched_text = self._match(ctx)
        evidence = [f"fallback rule '{rule.name}' matched"]
        if matched_text:
            evidence.append(f"error text: {matched_text[:300]}")
        if ctx.history_summary():
            evidence.append(ctx.history_summary())  # type: ignore[arg-type]
        related = ctx.possibly_related_commits
        suspected = related[0].sha if related and rule.classification is Classification.NON_RETRIABLE else None
        if suspected:
            evidence.append(f"possibly related commit {suspected[:7]} (heuristic, not confirmed)")

        payload = AIAnalysisPayload(
            classification=rule.classification,
            confidence=min(rule.confidence, FALLBACK_MAX_CONFIDENCE),
            root_cause=rule.root_cause,
            evidence=evidence,
            suggested_fix=rule.suggested_fix,
            # Never propose a retry from rules alone (FR-045, fail closed).
            proposed_action=ProposedAction.MANUAL_INVESTIGATION,
            suspected_commit=suspected,
            explanation=(
                f"AI analysis was not available ({reason}). This is a rule-based estimate with "
                f"confidence capped at {FALLBACK_MAX_CONFIDENCE}; it cannot authorize automatic retries, "
                "so a human should confirm the cause and decide on recovery."
            ),
        )
        return AnalysisResult.from_payload(
            payload,
            source=AnalysisSource.FALLBACK,
            provider=self.name,
            missing_evidence=[m.describe() for m in ctx.missing if m.kind != "not_applicable"],
            fallback_reason=reason,
        )

    def _match(self, ctx: InvestigationContext) -> tuple[Rule, str | None]:
        if ctx.failure_type is FailureType.DAG_RUN_FAILURE:
            return DAG_RUN_RULE, ctx.original_error
        candidates = [ctx.original_error or ""]
        if ctx.task_log:
            candidates.append("\n".join(ctx.task_log.splitlines()[-LOG_TAIL_LINES:]))
        for text in candidates:
            for rule in RULES:
                m = rule.pattern.search(text)
                if m:
                    line = next((ln for ln in text.splitlines() if rule.pattern.search(ln)), m.group(0))
                    return rule, line.strip()
        return UNKNOWN_RULE, ctx.original_error
