"""Offline simulation of the six SRS scenarios (SRS Part 9 ``simulate``).

Safety: every scenario builds **mock** Airflow, GitHub, Linear and AI clients directly
and a throw-away SQLite database in a temporary directory. Configuration is not used to
choose integrations, so ``oncall-agent simulate`` can never reach a real system — even
on a host configured for production. Any "Airflow change" shown is applied to the
in-memory mock only.

Each scenario runs the real agent pipeline (detection → investigation → analysis →
policy → recovery/approval → Linear) and checks the outcome against the expected
decision. Exit code 0 means every scenario behaved as expected.
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

from .agent import Agent, CycleReport
from .config import Settings, load_settings
from .factory import Integrations
from .integrations.airflow import DagRun, TaskInstance
from .integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient
from .integrations.mocks.sample_data import AirflowSample, build_airflow_sample
from .models import Incident
from .policy import Decision, PolicyDecision
from .providers.mock import MockAIProvider
from .services.audit import AuditService
from .store import Store

RERUN_REPLY = json.dumps({
    "classification": "uncertain", "confidence": 0.6,
    "root_cause": "The transform failed on a field the upstream export may have renamed; re-running the whole run may be needed once the export is confirmed.",
    "evidence": ["KeyError: 'customer_tier'", "fetch_customers succeeded in this run"],
    "suggested_fix": "Confirm the upstream export, then re-run the failed tasks of this run.",
    "proposed_action": "rerun_dag", "suspected_commit": None,
    "explanation": "Re-running is plausible but not certain; a human should approve it.",
})  # fmt: skip


@dataclass
class ScenarioResult:
    key: str
    title: str
    passed: bool
    incident: Incident | None
    decision: PolicyDecision | None
    expected: Decision
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class Env:
    agent: Agent
    airflow: MockAirflowClient
    linear: MockLinearClient
    ai: MockAIProvider


def _sample(dags: set[str], with_import_errors: bool = False) -> AirflowSample:
    full = build_airflow_sample()
    keep = AirflowSample()
    keep.dags = {k: v for k, v in full.dags.items() if k in dags}
    keep.structure = {k: v for k, v in full.structure.items() if k in dags}
    keep.runs = [r for r in full.runs if r.dag_id in dags]
    keep.task_instances = [t for t in full.task_instances if t.dag_id in dags]
    keep.logs = {k: v for k, v in full.logs.items() if k[0] in dags}
    keep.sources = {k: v for k, v in full.sources.items() if k in dags}
    keep.import_errors = full.import_errors if with_import_errors else []
    return keep


def _env(workdir: Path, dags: set[str]) -> Env:
    settings: Settings = load_settings(
        {
            "APP_ENV": "development",
            "ONCALL_INTEGRATION_MODE": "mock",
            "AI_PROVIDER": "mock",
            "ONCALL_DATABASE_PATH": str(workdir / "simulation.db"),
            "ONCALL_LOG_DIR": str(workdir / "logs"),
            "ONCALL_TRUSTED_APPROVERS": "alice@example.com",
        },
        env_file=workdir / "no.env",
    )
    airflow, linear, ai = MockAirflowClient(_sample(dags)), MockLinearClient(), MockAIProvider()
    store = Store(settings.storage.database_path).open()
    audit = AuditService(store, settings.logging.dir / "audit.jsonl")
    agent = Agent(settings, store, audit, Integrations(airflow, MockGitHubClient(), linear, mode="mock", ai=ai))
    return Env(agent, airflow, linear, ai)


def _only_incident(env: Env, report: CycleReport) -> tuple[Incident, PolicyDecision | None]:
    incident = env.agent.store.list_incidents()[0]
    return incident, report.decisions.get(incident.id)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- scenarios


def s1_retriable(env: Env, r: ScenarioResult) -> None:
    report = env.agent.run_once()
    r.incident, r.decision = _only_incident(env, report)
    clears = [c for c in env.airflow.write_calls if c[0] == "clear_task_instances"]
    r.notes.append(f"mock Airflow: cleared {clears[0][1]['task_ids'] if clears else 'nothing'} in run {r.incident.dag_run_id}")
    second = env.agent.run_once()
    outcome = [v.outcome for v in second.verifications]
    r.notes.append(f"next cycle: retry {outcome[0] if outcome else 'not verified'}; incident {env.agent.store.get_incident(r.incident.id).status.value}")
    if outcome != ["succeeded"]:
        r.failures.append("the retry was not verified as successful")


def s2_non_retriable(env: Env, r: ScenarioResult) -> None:
    report = env.agent.run_once()
    r.incident, r.decision = _only_incident(env, report)
    if env.airflow.write_calls:
        r.failures.append("Airflow was changed for a non-retriable failure")
    if not (r.incident.suspected_commit or "").startswith("a1b2c3d"):
        r.failures.append("the suspected commit was not identified")
    r.notes.append(f"suspected commit {(r.incident.suspected_commit or 'none')[:7]}; assigned to {r.incident.assignee or 'nobody'}")


def s3_uncertain(env: Env, r: ScenarioResult) -> None:
    report = env.agent.run_once()
    r.incident, r.decision = _only_incident(env, report)
    if env.airflow.write_calls:
        r.failures.append("Airflow was changed for an uncertain failure")


def s4_duplicate(env: Env, r: ScenarioResult) -> None:
    env.agent.run_once()
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    run_id = f"manual__{end.isoformat()}"
    env.airflow.runs[("customer_sync", run_id)] = DagRun(dag_id="customer_sync", dag_run_id=run_id, state="failed", start_date=end - timedelta(minutes=2), end_date=end)
    env.airflow.tis[("customer_sync", run_id, "transform_customers", -1)] = TaskInstance(
        dag_id="customer_sync", dag_run_id=run_id, task_id="transform_customers", state="failed", try_number=1, end_date=end
    )
    env.airflow.logs[("customer_sync", run_id, "transform_customers", 1)] = "Traceback (most recent call last):\nKeyError: 'customer_tier'"
    report = env.agent.run_once()
    incidents = env.agent.store.list_incidents()
    r.incident = incidents[0]
    r.decision = None
    if report.outcomes.get("duplicate") != 1 or len(incidents) != 1:
        r.failures.append(f"expected one duplicate of one incident, got {dict(report.outcomes)} and {len(incidents)} incident(s)")
    if len(env.linear.issues) != 1:
        r.failures.append(f"expected 1 Linear issue, found {len(env.linear.issues)}")
    r.notes.append(f"same incident #{r.incident.id}: {r.incident.occurrence_count} occurrences, 1 Linear issue ({r.incident.linear_issue_identifier}) updated with a comment")


def s5_ai_unavailable(env: Env, r: ScenarioResult) -> None:
    env.ai.unavailable = True
    report = env.agent.run_once()
    r.incident, r.decision = _only_incident(env, report)
    if r.incident.analysis_source is None or r.incident.analysis_source.value != "fallback":
        r.failures.append("fallback analysis was not used")
    if (r.incident.confidence or 0) > 0.5:
        r.failures.append("fallback confidence exceeded 0.5")
    if env.airflow.write_calls:
        r.failures.append("Airflow was changed although only fallback analysis was available")
    r.notes.append(f"fallback reason: {r.incident.analysis_fallback_reason}")


def s6_restricted(env: Env, r: ScenarioResult) -> None:
    env.ai.responses.append(RERUN_REPLY)
    report = env.agent.run_once()
    r.incident, r.decision = _only_incident(env, report)
    issue_id = r.incident.linear_issue_id
    if issue_id is None:
        r.failures.append("no Linear issue to approve on")
        return
    env.linear.comment_as(issue_id, "/approve", "user-mallory")
    env.agent.run_once()
    if env.airflow.write_calls:
        r.failures.append("an untrusted approval executed the action")
    r.notes.append("'/approve' from mallory@example.com (not trusted): rejected, nothing executed")
    env.linear.add_label_as(issue_id, "oncall:approved", "user-alice")
    env.agent.run_once()
    env.linear.add_label_as(issue_id, "oncall:approved", "user-alice")  # approval received twice
    env.agent.run_once()
    clears = [c for c in env.airflow.write_calls if c[0] == "clear_task_instances"]
    approval = env.agent.store.list_approvals(r.incident.id)[0]
    r.notes.append(f"label 'oncall:approved' from alice@example.com (trusted): approval {approval.status.value}; mock Airflow cleared {clears[0][1]['task_ids'] if clears else 'nothing'}")
    if len(clears) != 1:
        r.failures.append(f"expected exactly one execution, got {len(clears)}")


SCENARIOS: list[tuple[str, str, set[str], Decision, Callable[[Env, ScenarioResult], None]]] = [
    ("retriable", "Retriable task failure", {"sales_etl"}, Decision.AUTO_RETRY, s1_retriable),
    ("non_retriable", "Non-retriable task failure", {"customer_sync"}, Decision.ESCALATE, s2_non_retriable),
    ("uncertain", "Uncertain failure", {"ml_feature_build"}, Decision.ESCALATE, s3_uncertain),
    ("duplicate", "Duplicate incident", {"customer_sync"}, Decision.ESCALATE, s4_duplicate),
    ("ai_unavailable", "AI provider unavailable", {"sales_etl"}, Decision.ESCALATE, s5_ai_unavailable),
    ("restricted", "Restricted operation requiring human approval", {"customer_sync"}, Decision.REQUEST_APPROVAL, s6_restricted),
]

EXPECTED_ACTION = {
    Decision.AUTO_RETRY: "automatic retry of the failed task and its upstream_failed children",
    Decision.REQUEST_APPROVAL: "request human approval in Linear; run once after a trusted approval",
    Decision.ESCALATE: "escalate to a human via Linear; no Airflow change",
    Decision.NO_ACTION: "escalate for human confirmation",
}


def run_scenario(key: str, title: str, dags: set[str], expected: Decision, fn: Callable[[Env, ScenarioResult], None]) -> ScenarioResult:
    with tempfile.TemporaryDirectory(prefix="oncall-sim-") as tmp:
        env = _env(Path(tmp), dags)
        result = ScenarioResult(key, title, False, None, None, expected)
        try:
            fn(env, result)
            if result.decision is not None and result.decision.decision is not expected:
                result.failures.append(f"expected decision {expected.value}, got {result.decision.decision.value}")
            if result.incident is not None:
                result.incident = env.agent.store.get_incident(result.incident.id)  # final state
        except Exception as exc:  # report, never crash the whole simulation
            result.failures.append(f"scenario crashed: {type(exc).__name__}: {exc}")
        finally:
            env.agent.shutdown()
            env.agent.store.close()
        result.passed = not result.failures
        return result


def _print(result: ScenarioResult, index: int, total: int, verbose: bool, out: TextIO) -> None:
    out.write(f"\n[{index}/{total}] {result.title}  ...  {'PASS' if result.passed else 'FAIL'}\n")
    inc = result.incident
    if inc is not None:
        s = inc.context_summary or {}
        out.write(f"  Failure       : {inc.dag_id}{'.' + inc.task_id if inc.task_id else ''} — {inc.normalized_error}\n")
        out.write(f"  Investigation : collected {', '.join(s.get('collected', [])) or 'nothing'}"
                  f"; missing {', '.join(inc.missing_evidence) or 'none'}; {s.get('history') or 'no history'}\n")  # fmt: skip
        if inc.classification is not None:
            out.write(f"  Analysis      : {inc.classification.value} (confidence {inc.confidence:.2f}, {inc.analysis_source.value if inc.analysis_source else '?'}) "
                      f"→ proposes {inc.proposed_action.value if inc.proposed_action else '-'}\n")  # fmt: skip
            out.write(f"  Root cause    : {inc.root_cause}\n")
    if result.decision is not None:
        out.write(f"  Policy        : {result.decision.decision.value} — {'; '.join(result.decision.reasons)}\n")
    out.write(f"  Expected      : {EXPECTED_ACTION[result.expected]}\n")
    for note in result.notes:
        out.write(f"  Outcome       : {note}\n")
    if inc is not None:
        out.write(f"  Final state   : incident #{inc.id} {inc.status.value}; Linear {inc.linear_issue_identifier or '-'}\n")
    if verbose and result.decision is not None:
        out.write("  Policy checks :\n")
        for c in result.decision.checks:
            out.write(f"    [{'x' if c.passed else ' '}] {c.name:<26} {c.detail}\n")
    if verbose and inc is not None:
        if inc.analysis_explanation:
            out.write(f"  Explanation   : {inc.analysis_explanation}\n")
        for e in inc.evidence:
            out.write(f"  Evidence      : {e}\n")
        if inc.ownership_note:
            out.write(f"  Ownership     : {inc.ownership_note}\n")
    for f in result.failures:
        out.write(f"  !! {f}\n")


def run_simulation(out: TextIO, *, verbose: bool = False, only: list[str] | None = None) -> int:
    chosen = [s for s in SCENARIOS if not only or s[0] in only]
    out.write("Simulation — mock Airflow, GitHub, Linear and AI; temporary databases. No real system is contacted.\n")
    results = []
    for i, (key, title, dags, expected, fn) in enumerate(chosen, 1):
        result = run_scenario(key, title, dags, expected, fn)
        _print(result, i, len(chosen), verbose, out)
        results.append(result)
    passed = sum(r.passed for r in results)
    out.write(f"\n{passed}/{len(results)} scenarios behaved as expected.\n")
    return 0 if passed == len(results) else 1
