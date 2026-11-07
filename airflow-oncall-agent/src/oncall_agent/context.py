"""Diagnostic context collection (FR-021..FR-029, NFR-016).

For one incident the collector gathers, each source independently:

==============  ==========================================================  ======
source          content                                                     FR
==============  ==========================================================  ======
task_log        log of the failed try (tail, truncated)                     021
dag_info        owners and file location                                    (078)
dag_source      DAG file as parsed by Airflow (truncated)                   022
run_history     last 10 runs of the DAG + this task's state in each         023/024
commits         commits touching the DAG file in the lookback window        025
patches         per-commit file patches (truncated)                         026
==============  ==========================================================  ======

**Missing evidence (FR-028/029).** A source that cannot be read is recorded in
``missing`` with a reason and a kind (``unavailable``, ``not_applicable``,
``not_configured``). Collection never raises because of an integration failure.
"No commits found" is reported as an *observation*, never as proof that code did
not change, and missing logs are never presented as a cause.

**Possible code relationships (FR-027).** Each commit is scored by transparent
heuristics (below). A high score means "worth looking at", not "confirmed cause":
the output always says ``possible``.

* +3  the patch adds/removes a line containing an identifier from the error line
       (e.g. ``'customer_tier'`` in ``KeyError: 'customer_tier'``)
* +2  the traceback points into a file the commit changed
* +1  committed within 24 hours before the failure
* −2  the DAG ran successfully *after* the commit (less likely to be the cause)
* commits made after the failure are excluded from scoring (they cannot be the cause)
"""
from __future__ import annotations

import logging
import posixpath
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from .config import AISettings, GitHubSettings
from .exceptions import IntegrationError
from .integrations.airflow import AirflowClient
from .integrations.github import CommitDetail, CommitSummary, GitHubClient
from .models import FailureType, Incident

log = logging.getLogger(__name__)

HISTORY_RUNS = 10  # FR-023
RECENT_COMMIT_WINDOW = timedelta(hours=24)
SUSPECT_THRESHOLD = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- context model


class MissingEvidence(BaseModel):
    source: str
    kind: str  # "unavailable" | "not_applicable" | "not_configured"
    reason: str

    def describe(self) -> str:
        return f"{self.source}: {self.reason}"


class RunSummary(BaseModel):
    dag_run_id: str
    state: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    task_state: str | None = None  # state of the incident's task in this run (FR-024)


class FileEvidence(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    patch_truncated: bool = False


class CommitEvidence(BaseModel):
    sha: str
    title: str
    author_name: str | None = None
    author_email: str | None = None
    author_login: str | None = None
    committed_at: datetime | None = None
    url: str | None = None
    files: list[FileEvidence] = Field(default_factory=list)
    relation_score: int = 0
    relation_reasons: list[str] = Field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def possibly_related(self) -> bool:
        return self.relation_score >= SUSPECT_THRESHOLD


class InvestigationContext(BaseModel):
    incident_id: int | None
    failure_type: FailureType
    dag_id: str
    task_id: str | None
    dag_run_id: str | None
    original_error: str | None
    normalized_error: str
    failure_time: datetime | None = None

    task_log: str | None = None
    task_log_truncated: bool = False
    dag_owners: list[str] = Field(default_factory=list)
    dag_file: str | None = None
    dag_source: str | None = None
    dag_source_truncated: bool = False
    recent_runs: list[RunSummary] = Field(default_factory=list)
    commits: list[CommitEvidence] = Field(default_factory=list)
    commit_search: str | None = None  # what was searched, for transparency
    missing: list[MissingEvidence] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=_utcnow)

    # ----------------------------------------------------------------- derived views

    @property
    def possibly_related_commits(self) -> list[CommitEvidence]:
        return sorted((c for c in self.commits if c.possibly_related), key=lambda c: -c.relation_score)

    @property
    def collected_sources(self) -> list[str]:
        sources = []
        if self.task_log:
            sources.append("task_log")
        if self.dag_source:
            sources.append("dag_source")
        if self.recent_runs:
            sources.append("run_history")
        if self.commit_search is not None and not any(m.source == "commits" for m in self.missing):
            sources.append("commits")
        return sources

    def history_summary(self) -> str | None:
        if not self.recent_runs:
            return None
        total = len(self.recent_runs)
        ok = sum(1 for r in self.recent_runs if r.state == "success")
        text = f"{ok} of the last {total} runs succeeded"
        if self.task_id:
            task_failed = sum(1 for r in self.recent_runs if r.task_state == "failed")
            text += f"; task {self.task_id} failed in {task_failed} of them"
        return text

    def report_summary(self, log_tail_lines: int = 30, max_log_chars: int = 3000) -> dict[str, Any]:
        """What reports (Linear, CLI) need after the in-memory context is gone. Stored on
        the incident; text is redacted before it is persisted."""
        from .logging_config import redact  # local import: logging_config has no deps on us

        tail = None
        if self.task_log:
            tail = "\n".join(self.task_log.splitlines()[-log_tail_lines:])[-max_log_chars:]
        return {
            "dag_owners": self.dag_owners,
            "dag_file": self.dag_file,
            "history": self.history_summary(),
            "observations": [redact(o) for o in self.observations],
            "commit_search": self.commit_search,
            "commits": [
                {
                    "sha": c.sha, "title": redact(c.title), "author_name": c.author_name, "author_email": c.author_email,
                    "author_login": c.author_login, "committed_at": c.committed_at.isoformat() if c.committed_at else None,
                    "url": c.url, "relation_score": c.relation_score, "relation_reasons": c.relation_reasons,
                    "possibly_related": c.possibly_related,
                }  # fmt: skip
                for c in self.commits
            ],
            "collected": self.collected_sources,
            "not_applicable": [m.describe() for m in self.missing if m.kind == "not_applicable"],
            "log_tail": redact(tail) if tail else None,
        }

    def summary(self) -> dict[str, Any]:
        """Compact description for audit records and reports."""
        return {
            "collected": self.collected_sources,
            "missing": [m.describe() for m in self.missing if m.kind != "not_applicable"],
            "not_applicable": [m.describe() for m in self.missing if m.kind == "not_applicable"],
            "history": self.history_summary(),
            "commits_found": len(self.commits),
            "possibly_related_commits": [f"{c.short_sha} (score {c.relation_score})" for c in self.possibly_related_commits],
        }


# --------------------------------------------------------------------------- heuristics (FR-027)

_QUOTED = re.compile(r"""['"]([A-Za-z_][\w.\-]{2,})['"]""")
_IDENT = re.compile(r"\b([A-Za-z_]\w*_\w+|[a-z]+[A-Z]\w+)\b")  # snake_case or camelCase
_TRACE_FILE = re.compile(r'File "([^"]+)", line \d+')


def error_identifiers(error_line: str | None) -> set[str]:
    """Identifiers in the error line that a code change could plausibly have touched."""
    if not error_line:
        return set()
    found = set(_QUOTED.findall(error_line)) | set(_IDENT.findall(error_line))
    return {f for f in found if len(f) >= 4 and not f.startswith("<")}


def traceback_files(log_text: str | None) -> set[str]:
    return set(_TRACE_FILE.findall(log_text or ""))


def _changed_lines(patch: str) -> str:
    return "\n".join(line for line in patch.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---")))


def score_commit(
    commit: CommitEvidence,
    *,
    failure_time: datetime | None,
    identifiers: set[str],
    trace_files: set[str],
    last_success_start: datetime | None,
) -> None:
    """Fill ``relation_score`` / ``relation_reasons`` in place (see module docstring)."""
    reasons: list[str] = []
    score = 0
    if failure_time and commit.committed_at and commit.committed_at > failure_time:
        commit.relation_score, commit.relation_reasons = 0, ["committed after the failure; cannot be the cause"]
        return
    for f in commit.files:
        if f.patch:
            changed = _changed_lines(f.patch)
            hits = sorted(i for i in identifiers if i in changed)
            if hits:
                score += 3
                reasons.append(f"patch to {f.filename} changes lines containing {', '.join(repr(h) for h in hits)} from the error")
                break
    changed_names = {f.filename for f in commit.files}
    for tf in trace_files:
        if any(tf.endswith("/" + name) or tf.endswith(name) or posixpath.basename(tf) == posixpath.basename(name) for name in changed_names):
            score += 2
            reasons.append(f"traceback passes through {tf}, which this commit changed")
            break
    if failure_time and commit.committed_at and failure_time - commit.committed_at <= RECENT_COMMIT_WINDOW:
        hours = (failure_time - commit.committed_at).total_seconds() / 3600
        score += 1
        reasons.append(f"committed {hours:.1f}h before the failure")
    if last_success_start and commit.committed_at and commit.committed_at < last_success_start:
        score -= 2
        reasons.append("the DAG succeeded after this commit, so it is less likely to be the cause")
    commit.relation_score = max(score, 0)
    commit.relation_reasons = reasons or ["touches the DAG file; no specific link to the error found"]


def _tail(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return "…[earlier log lines truncated]…\n" + text[-limit:], True


def _head(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[truncated]…", True


# --------------------------------------------------------------------------- collector


class ContextCollector:
    def __init__(
        self,
        airflow: AirflowClient,
        github: GitHubClient,
        github_settings: GitHubSettings,
        ai_settings: AISettings,
        *,
        clock=None,
    ) -> None:
        self.airflow = airflow
        self.github = github
        self.gh = github_settings
        self.limits = ai_settings
        self._clock = clock or _utcnow
        # Integration failures seen during the last collect(), for auditing.
        self.integration_failures: list[tuple[str, str]] = []

    def collect(self, incident: Incident, *, known_log: str | None = None) -> InvestigationContext:
        """Collect everything available. Never raises because of an integration failure."""
        self.integration_failures = []
        ctx = InvestigationContext(
            incident_id=incident.id,
            failure_type=incident.failure_type,
            dag_id=incident.dag_id,
            task_id=incident.task_id,
            dag_run_id=incident.dag_run_id,
            original_error=incident.original_error,
            normalized_error=incident.normalized_error,
            failure_time=incident.last_detected_at,
            collected_at=self._clock(),
        )
        steps = (
            ("task_log", self._task_log),
            ("dag_source", self._dag_info_and_source),
            ("run_history", self._run_history),
            ("commits", self._commits),
        )
        for source, step in steps:
            try:
                step(ctx, known_log)
            except Exception as exc:  # a bug in one step must not lose the others (FR-028)
                log.exception("Context step %s failed", source)
                ctx.missing.append(MissingEvidence(source=source, kind="unavailable", reason=f"internal error: {type(exc).__name__}"))
        return ctx

    def _unavailable(self, ctx: InvestigationContext, source: str, exc: Exception, action: str) -> None:
        ctx.missing.append(MissingEvidence(source=source, kind="unavailable", reason=str(exc)[:300]))
        self.integration_failures.append((action, str(exc)[:300]))

    # ----------------------------------------------------------------- FR-021

    def _task_log(self, ctx: InvestigationContext, known_log: str | None) -> None:
        if ctx.failure_type is FailureType.DAG_RUN_FAILURE:
            ctx.missing.append(MissingEvidence(source="task_log", kind="not_applicable", reason="the DAG run failed without a failed task, so there is no task log"))
            return
        text = known_log
        if text is None and ctx.failure_type is FailureType.IMPORT_ERROR:
            try:
                text = next((e.stack_trace for e in self.airflow.list_import_errors() if e.filename == ctx.dag_id), None)
            except IntegrationError as exc:
                self._unavailable(ctx, "task_log", exc, "airflow.list_import_errors")
                return
        if text is None and ctx.failure_type is FailureType.TASK_FAILURE:
            assert ctx.task_id and ctx.dag_run_id
            try:
                ti = self.airflow.get_task_instance(ctx.dag_id, ctx.dag_run_id, ctx.task_id)
                text = self.airflow.get_task_log(ctx.dag_id, ctx.dag_run_id, ctx.task_id, max(ti.try_number, 1))
            except IntegrationError as exc:
                self._unavailable(ctx, "task_log", exc, "airflow.get_task_log")
                return
        if not text:
            ctx.missing.append(MissingEvidence(source="task_log", kind="unavailable", reason="the log is empty or no longer available"))
            return
        ctx.task_log, ctx.task_log_truncated = _tail(text, self.limits.max_log_chars)

    # ----------------------------------------------------------------- FR-022

    def _dag_info_and_source(self, ctx: InvestigationContext, _: str | None) -> None:
        if ctx.failure_type is FailureType.IMPORT_ERROR:
            ctx.dag_file = ctx.dag_id
            ctx.missing.append(MissingEvidence(source="dag_source", kind="not_applicable", reason="the file failed to import, so Airflow has no parsed DAG source; see the stack trace"))
            return
        try:
            dag = self.airflow.get_dag(ctx.dag_id)
            ctx.dag_owners = [o for o in dag.owners if o]
            ctx.dag_file = dag.relative_fileloc or dag.fileloc
        except IntegrationError as exc:
            self._unavailable(ctx, "dag_info", exc, "airflow.get_dag")
        try:
            source = self.airflow.get_dag_source(ctx.dag_id)
            ctx.dag_source, ctx.dag_source_truncated = _head(source, self.limits.max_source_chars)
        except IntegrationError as exc:
            self._unavailable(ctx, "dag_source", exc, "airflow.get_dag_source")

    # ----------------------------------------------------------------- FR-023/024

    def _run_history(self, ctx: InvestigationContext, _: str | None) -> None:
        if ctx.failure_type is FailureType.IMPORT_ERROR:
            ctx.missing.append(MissingEvidence(source="run_history", kind="not_applicable", reason="import errors are not tied to a DAG run"))
            return
        try:
            runs = self.airflow.get_recent_dag_runs(ctx.dag_id, limit=HISTORY_RUNS)
        except IntegrationError as exc:
            self._unavailable(ctx, "run_history", exc, "airflow.get_recent_dag_runs")
            return
        task_errors = 0
        for run in runs:
            task_state = None
            if ctx.task_id:
                try:
                    task_state = self.airflow.get_task_instance(ctx.dag_id, run.dag_run_id, ctx.task_id).state
                except IntegrationError as exc:
                    task_errors += 1
                    task_state = "unknown" if exc.status_code != 404 else "absent"
            ctx.recent_runs.append(RunSummary(dag_run_id=run.dag_run_id, state=run.state, start_date=run.start_date, end_date=run.end_date, task_state=task_state))
        if task_errors:
            ctx.observations.append(f"task state could not be read for {task_errors} of {len(runs)} historical runs")
        summary = ctx.history_summary()
        if summary:
            ctx.observations.append(summary)

    # ----------------------------------------------------------------- FR-025..027

    def _commit_path(self, ctx: InvestigationContext) -> str | None:
        if not ctx.dag_file:
            return None
        relative = ctx.dag_file
        if relative.startswith("/"):
            relative = posixpath.basename(relative)  # absolute fileloc: only the name is reliable
        return posixpath.join(self.gh.dag_path_prefix, relative) if self.gh.dag_path_prefix else relative

    def _commits(self, ctx: InvestigationContext, _: str | None) -> None:
        path = self._commit_path(ctx)
        if path is None:
            ctx.missing.append(MissingEvidence(source="commits", kind="unavailable", reason="DAG file location unknown, so commits could not be searched"))
            return
        anchor = ctx.failure_time or self._clock()
        since = anchor - timedelta(days=self.gh.lookback_days)
        ctx.commit_search = f"commits to {path} since {since.date().isoformat()}"
        try:
            summaries: list[CommitSummary] = self.github.list_commits(path=path, since=since, max_items=self.gh.max_commits)
        except IntegrationError as exc:
            self._unavailable(ctx, "commits", exc, "github.list_commits")
            return
        if not summaries:
            ctx.observations.append(f"No commits found ({ctx.commit_search}). This is not proof that nothing changed (e.g. shared modules or config).")
            return

        budget = self.limits.max_patch_chars
        for summary in summaries:
            evidence = CommitEvidence(
                sha=summary.sha, title=summary.title, author_name=summary.author_name, author_email=summary.author_email,
                author_login=summary.author_login, committed_at=summary.committed_at, url=summary.html_url,
            )  # fmt: skip
            try:
                detail: CommitDetail = self.github.get_commit(summary.sha)
                for f in detail.files:
                    patch, truncated = (None, False)
                    if f.patch is not None and budget > 0:
                        patch, truncated = _head(f.patch, budget)
                        budget -= len(patch)
                    elif f.patch is not None:
                        truncated = True
                    evidence.files.append(FileEvidence(filename=f.filename, status=f.status, additions=f.additions, deletions=f.deletions, patch=patch, patch_truncated=truncated))
                    if f.patch is None:
                        ctx.missing.append(MissingEvidence(source=f"patch:{summary.sha[:7]}:{f.filename}", kind="unavailable", reason="GitHub did not provide a patch (binary or too large)"))
            except IntegrationError as exc:
                self._unavailable(ctx, f"patch:{summary.sha[:7]}", exc, "github.get_commit")
            ctx.commits.append(evidence)

        identifiers = error_identifiers(ctx.original_error)
        trace = traceback_files(ctx.task_log)
        last_success = max((r.start_date for r in ctx.recent_runs if r.state == "success" and r.start_date), default=None)
        for commit in ctx.commits:
            score_commit(commit, failure_time=ctx.failure_time, identifiers=identifiers, trace_files=trace, last_success_start=last_success)
        related = ctx.possibly_related_commits
        if related:
            ctx.observations.append(
                "Possibly related change(s) (heuristic, not confirmed): " + "; ".join(f"{c.short_sha} {c.title!r} — {', '.join(c.relation_reasons)}" for c in related)
            )
