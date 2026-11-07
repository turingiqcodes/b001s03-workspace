"""Command-line interface (SRS Part 9).

Exit codes (documented in README.md):
    0  success
    1  command ran but found a problem (e.g. an integration is unreachable)
    2  usage error (raised by argparse)
    3  configuration error: the agent refuses to start
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import TextIO

from . import __version__
from .config import Settings, load_settings, validate_settings
from .exceptions import ConfigError, ConfigIssue, OncallError
from .factory import build_audit, build_integrations, build_store
from .logging_config import redact, setup_logging
from .models import IncidentStatus
from .store import Store

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

log = logging.getLogger("oncall_agent.cli")


def _print_issues(issues: list[ConfigIssue], out: TextIO) -> None:
    for issue in issues:
        out.write(f"  {issue.severity.upper():<7} {issue.key}: {issue.message}\n")


def _describe(settings: Settings, out: TextIO) -> None:
    out.write(f"  environment       : {settings.app_env}\n")
    out.write(f"  integrations      : {settings.integration_mode}\n")
    out.write(f"  AI provider       : {settings.ai.provider}" + (f" ({settings.ai.groq_model})" if settings.ai.provider == "groq" and settings.ai.groq_model else "") + "\n")
    if not settings.use_mocks:
        unset = "(not set)"
        out.write(f"  Airflow           : {settings.airflow.base_url or unset} (auth: {settings.airflow.auth_method})\n")
        out.write(f"  GitHub repository : {settings.github.repository or unset}\n")
        out.write(f"  Linear team       : {settings.linear.team_id or unset}\n")
    out.write(f"  database          : {settings.storage.database_path}\n")
    out.write(
        f"  automatic retries : {'enabled' if settings.auto_retries_enabled else 'DISABLED'}"
        f" (per incident {settings.policy.max_auto_retries}, per hour {settings.policy.global_max_retries_per_hour})\n"
    )
    out.write(f"  trusted approvers : {len(settings.approval.trusted_approvers)}\n")
    out.write(
        f"  MCP writes        : airflow={settings.mcp.allow_airflow_writes} linear={settings.mcp.allow_linear_writes}\n"
    )


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    """Validate configuration (AT-001/AT-002)."""
    out.write("Configuration\n")
    try:
        settings = load_settings(env_file=args.env_file, validate=False)
    except ConfigError as exc:
        out.write("  ERROR   configuration could not be loaded\n")
        _print_issues(exc.issues or [ConfigIssue("error", "config", str(exc))], out)
        return EXIT_CONFIG

    issues = validate_settings(settings)
    _describe(settings, out)
    _print_issues(issues, out)
    if any(i.severity == "error" for i in issues):
        out.write("Result: configuration INVALID — the agent will not start.\n")
        return EXIT_CONFIG
    out.write("Result: configuration OK\n\n")

    setup_logging(level="WARNING", secrets=settings.secret_values())
    ok = _check_connectivity(settings, out)
    out.write("Result: all checks passed\n" if ok else "Result: one or more checks FAILED\n")
    return EXIT_OK if ok else EXIT_FAILURE


def _check_connectivity(settings: Settings, out: TextIO) -> bool:
    """Database and integration reachability (SRS Part 9 `check`)."""
    out.write(f"Connectivity ({settings.integration_mode} integrations)\n")
    ok = True

    def line(name: str, passed: bool, detail: str) -> None:
        out.write(f"  {'OK  ' if passed else 'FAIL'}  {name:<9} {redact(detail)}\n")

    try:
        with Store(settings.storage.database_path) as store:
            line("database", True, f"{settings.storage.database_path} (schema v{store.schema_version()})")
    except OncallError as exc:
        ok = False
        line("database", False, str(exc))

    try:
        integrations = build_integrations(settings)
    except OncallError as exc:
        line("clients", False, str(exc))
        return False
    try:
        clients = (("airflow", integrations.airflow), ("github", integrations.github), ("linear", integrations.linear), ("ai", integrations.ai))
        for name, client in clients:
            if client is None:
                ok = False
                line(name, False, "not configured")
                continue
            try:
                info = client.check_connection()
                line(name, True, ", ".join(f"{k}={v}" for k, v in info.items()))
            except OncallError as exc:
                ok = False
                line(name, False, str(exc))
        # Labels are looked up, never created (decision A7); a missing label is a warning.
        try:
            wanted = _configured_labels(settings)
            found = integrations.linear.find_label_ids(wanted)
            missing = [n for n in wanted if n not in found]
            if missing:
                out.write(f"  WARN  labels    missing in Linear (create them manually): {', '.join(missing)}\n")
            else:
                line("labels", True, f"{len(found)} configured labels found")
        except OncallError as exc:
            out.write(f"  WARN  labels    could not be checked: {redact(str(exc))}\n")
    finally:
        integrations.close()
    return ok


def _configured_labels(settings: Settings) -> list[str]:
    labels = settings.linear.labels
    names = [labels.base, labels.approved, labels.import_error, labels.awaiting_approval, labels.escalated, *labels.classification.values()]
    return list(dict.fromkeys(n for n in names if n))


# ----------------------------------------------------------------------------- run / once


def _start(args: argparse.Namespace):
    """Load validated settings and build the agent. ConfigError → exit code 3."""
    from .agent import Agent

    settings = load_settings(env_file=args.env_file, validate=True)
    setup_logging(settings.logging.level, settings.logging.format, settings.logging.dir, secrets=settings.secret_values())
    store = build_store(settings)
    audit = build_audit(settings, store)
    integrations = build_integrations(settings)
    return Agent(settings, store, audit, integrations)


def _close(agent) -> None:
    agent.shutdown()
    agent.integrations.close()
    agent.store.close()


def cmd_run(args: argparse.Namespace, out: TextIO) -> int:
    """Continuous monitoring until SIGINT/SIGTERM."""
    agent = _start(args)
    stop = threading.Event()

    def handle(signum: int, _frame: object) -> None:  # first signal: finish the current cycle and stop
        log.warning("Received signal %s; stopping after the current cycle", signum)
        stop.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    out.write(f"Monitoring Airflow every {agent.settings.polling.interval_seconds}s "
              f"({agent.settings.integration_mode} integrations, AI: {agent.settings.ai.provider}). Ctrl+C to stop.\n")  # fmt: skip
    out.flush()
    try:
        cycles = agent.run(stop)
    finally:
        _close(agent)
    out.write(f"Stopped after {cycles} cycle(s).\n")
    return EXIT_OK


def cmd_once(args: argparse.Namespace, out: TextIO) -> int:
    """One full monitoring + investigation cycle, waiting for analyses."""
    agent = _start(args)
    try:
        report = agent.run_once(wait_for_analysis=True, timeout=agent.settings.ai.timeout_seconds * 4)
        out.write(f"Cycle: {report.summary()}\n")
        for incident_id, decision in sorted(report.decisions.items()):
            inc = agent.store.get_incident(incident_id)
            out.write(f"  #{incident_id:<4} {_name(inc):<45} {decision.decision.value:<17} → {inc.status.value}\n")
        for v in report.verifications:
            if v.outcome != "pending":
                out.write(f"  #{v.incident_id:<4} retry {v.outcome}: {v.detail}\n")
        for err in report.errors:
            out.write(f"  ERROR {redact(err)}\n")
        if report.failed_pollers:
            out.write(f"  ERROR polling failed for: {', '.join(report.failed_pollers)} (watermarks kept)\n")
        return EXIT_OK if report.ok else EXIT_FAILURE
    finally:
        _close(agent)


# ----------------------------------------------------------------------------- incidents / audit


def _name(inc) -> str:
    return f"{inc.dag_id}.{inc.task_id}" if inc.task_id else inc.dag_id


def _open_store(args: argparse.Namespace) -> Store:
    settings = load_settings(env_file=args.env_file, validate=False)
    return Store(settings.storage.database_path).open()


def cmd_incidents(args: argparse.Namespace, out: TextIO) -> int:
    with _open_store(args) as store:
        statuses = [IncidentStatus(s) for s in args.status] if args.status else None
        incidents = store.list_incidents(statuses, limit=args.limit)
    if args.json:
        out.write(json.dumps([i.model_dump(mode="json", exclude={"context_summary", "fingerprint_input"}) for i in incidents], indent=2) + "\n")
        return EXIT_OK
    if not incidents:
        out.write("No incidents.\n")
        return EXIT_OK
    out.write(f"{'ID':>4}  {'STATUS':<17} {'DAG / TASK':<42} {'CLASS':<13} {'CONF':>4}  {'ACTION':<20} {'LINEAR':<9} {'SEEN':>4} {'RETRY':>5}  LAST DETECTED\n")
    for i in incidents:
        out.write(
            f"{i.id:>4}  {i.status.value:<17} {_name(i)[:42]:<42} {(i.classification.value if i.classification else '-'):<13} "
            f"{(f'{i.confidence:.2f}' if i.confidence is not None else '-'):>4}  {(i.proposed_action.value if i.proposed_action else '-'):<20} "
            f"{(i.linear_issue_identifier or '-'):<9} {i.occurrence_count:>4} {i.retry_count:>5}  {i.last_detected_at:%Y-%m-%d %H:%M}\n"
        )  # fmt: skip
    return EXIT_OK


def cmd_audit(args: argparse.Namespace, out: TextIO) -> int:
    with _open_store(args) as store:
        incident = store.get_incident(args.incident_id)
        if incident is None:
            out.write(f"Incident {args.incident_id} not found.\n")
            return EXIT_FAILURE
        events = store.list_audit_events(args.incident_id)
    out.write(f"Incident #{incident.id}: {_name(incident)} — {incident.status.value}"
              f"{' — ' + incident.linear_issue_identifier if incident.linear_issue_identifier else ''}\n")  # fmt: skip
    for e in events:
        out.write(f"{e.timestamp:%Y-%m-%d %H:%M:%S}  {'ok  ' if e.success else 'FAIL'}  {e.event_type.value:<22} {e.actor:<22} {e.action:<26} {e.output_summary or ''}\n")
        if args.verbose and e.details:
            out.write("      " + json.dumps(e.details, default=str)[:2000] + "\n")
    out.write(f"{len(events)} event(s).\n")
    return EXIT_OK


def cmd_simulate(args: argparse.Namespace, out: TextIO) -> int:
    from .simulate import run_simulation

    setup_logging(level="ERROR")  # keep the output readable; results are printed
    return run_simulation(out, verbose=args.verbose, only=args.scenario or None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oncall-agent",
        description="Airflow Oncall Agent: monitor Airflow failures, investigate, manage Linear incidents and safely retry eligible tasks.",
        epilog="Exit codes: 0 ok, 1 problem found, 2 usage error, 3 configuration error.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="path to a .env file (default: <project>/.env, or $ONCALL_ENV_FILE)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_check = sub.add_parser("check", help="check configuration and connectivity to configured integrations")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="start continuous monitoring (stop with Ctrl+C / SIGTERM)")
    p_run.set_defaults(func=cmd_run)

    p_once = sub.add_parser("once", help="run one monitoring and investigation cycle, then exit")
    p_once.set_defaults(func=cmd_once)

    p_sim = sub.add_parser("simulate", help="run the six offline simulation scenarios (mocks only; no real system is contacted)")
    p_sim.add_argument("-v", "--verbose", action="store_true", help="show policy checks, evidence and explanations")
    p_sim.add_argument("--scenario", action="append", choices=["retriable", "non_retriable", "uncertain", "duplicate", "ai_unavailable", "restricted"],
                       help="run only this scenario (repeatable)")  # fmt: skip
    p_sim.set_defaults(func=cmd_simulate)

    p_inc = sub.add_parser("incidents", help="list known incidents and their current state")
    p_inc.add_argument("--status", action="append", choices=[s.value for s in IncidentStatus], help="filter by status (repeatable)")
    p_inc.add_argument("--limit", type=int, default=50, help="maximum number of incidents (default 50)")
    p_inc.add_argument("--json", action="store_true", help="machine-readable output")
    p_inc.set_defaults(func=cmd_incidents)

    p_audit = sub.add_parser("audit", help="show the audit history of one incident")
    p_audit.add_argument("incident_id", type=int, help="incident ID (see `incidents`)")
    p_audit.add_argument("-v", "--verbose", action="store_true", help="include event details")
    p_audit.set_defaults(func=cmd_audit)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    setup_logging(level="WARNING")  # commands raise this once settings are loaded
    try:
        return int(args.func(args, out))
    except ConfigError as exc:
        errors = [i for i in exc.issues if i.severity == "error"]
        out.write("Configuration error — the agent will not start:\n" if errors else f"Configuration error: {exc}\n")
        for issue in errors:
            out.write(f"  {issue.key}: {issue.message}\n")
        out.write("Run `oncall-agent check` for details.\n")
        return EXIT_CONFIG
    except OncallError as exc:
        out.write(f"Error: {redact(str(exc))}\n")
        return EXIT_FAILURE
    except KeyboardInterrupt:
        out.write("Interrupted.\n")
        return EXIT_FAILURE
