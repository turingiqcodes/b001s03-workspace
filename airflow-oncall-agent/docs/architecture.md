# Architecture — Airflow Oncall Agent v1.0

This document records the architecture and the design decisions approved during
planning (A1–A7), section by section in the order the phases built them.

## 1. Flow

```
Airflow ──poll──► Monitoring ──FailureEvent──► Fingerprint ──► Incident (dedupe)
                                                                  │
                         GitHub ──► Context Collector ◄───────────┘
                                         │
                                         ▼
                              Analysis (Groq │ fallback rules)
                                         │  AnalysisResult (validated)
                                         ▼
                              Deterministic Policy Engine
                     ┌───────────────────┼─────────────────────┐
                     ▼                   ▼                     ▼
             Recovery (retry)   Approval request         Escalate / no action
                     │                   │ (trusted human in Linear)
                     ▼                   ▼
                Verify retry      Execute once
                     └─────────► Linear incident ◄───────────┘
                                         │
                          SQLite state + JSON audit trail
```

The AI provider only ever produces an `AnalysisResult`. Nothing in the analysis path
holds an Airflow write client; only `services/recovery.py` does, and it acts only on a
policy decision or a validated, unused approval stored in SQLite.

## 2. Package layout

| Path (under `src/oncall_agent/`) | Responsibility |
|---|---|
| `config.py` | Layered config (env > .env > SSM > `config/<env>.json` > `config/default.json`) and validation |
| `logging_config.py` | Text/JSON logging, secret redaction |
| `exceptions.py` | Exception hierarchy |
| `models/` | Pydantic domain models: incidents, analysis schema, approvals, audit events |
| `store.py` | SQLite schema, migrations, transactions (Phase 2) |
| `integrations/` | Airflow, GitHub, Linear clients; `mocks/` offline equivalents (Phase 2) |
| `fingerprint.py` | Error normalization and SHA-256 fingerprints (Phase 3) |
| `services/monitoring.py` | Polling and watermarks (Phase 3) |
| `context.py`, `services/investigation.py` | Diagnostic context (Phase 4) |
| `providers/`, `analysis.py` | AI provider interface, Groq, offline mock, rule-based fallback, validation (Phase 5) |
| `policy.py` | Deterministic policy engine (Phase 6) |
| `services/recovery.py` | Safe task clearing and verification (Phase 6) |
| `services/incident_manager.py`, `ownership.py`, `reporting.py` | Linear incidents (Phase 7) |
| `services/approval.py` | Human approval workflow (Phase 7) |
| `services/audit.py` | Audit trail (Phase 2) |
| `mcp_servers/` | `oncall-airflow-mcp`, `oncall-linear-mcp` (Phase 8) |
| `agent.py`, `factory.py`, `cli.py`, `simulate.py` | Orchestration, wiring, CLI (Phases 3–9) |

## 3. Approved decisions

### A1 — `rerun_dag`
After approval, `rerun_dag` clears only the `failed` and `upstream_failed` task
instances of **that specific DAG run**. Successful tasks are never cleared and the
run keeps its identity. No new DAG run is triggered.

### A2 — Approvals
* Approval applies only to the pending restricted action (`rerun_dag`, `mark_success`)
  recorded on the incident, and must be dated **after** the request.
* Approvers must be in `ONCALL_TRUSTED_APPROVERS` (Linear user IDs or emails). An
  empty allowlist rejects every approval, except in mock mode.
* The approver is taken from Linear's label-change history (label approvals) or the
  comment author (`/approve`). Approvals made by the agent's own Linear user are
  rejected.
* A `retry_task` declined by policy is escalated; it is not approvable in v1.

### A3 — Retry counter
`Incident.retry_count` counts automatic retries over the incident's life (SRS §7.1).
The FR-050 "fewer than N retries" rule uses `cycle_retry_count`, which counts retries
since the incident was opened or last reopened. A resolved incident that fails again
is reopened (same Linear issue), resetting `cycle_retry_count` only.

### A4 — No-retry DAGs
`policy.no_retry_dags` in `config/*.json`. Retries are disabled globally when
`ONCALL_MAX_AUTO_RETRIES=0` or `ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR=0`.

### A5 — Mock vs real integrations
`ONCALL_INTEGRATION_MODE=mock|real`. Default: `mock` in development/test, `real` in
production. Production rejects `mock` integrations and the `mock` AI provider and
validates every required credential even if `mock` was requested; it never falls back
to mocks.

### A6 — Additional variables
`ONCALL_INTEGRATION_MODE`, `ONCALL_TRUSTED_APPROVERS`, `ONCALL_SSM_PREFIX`,
`ONCALL_MCP_ALLOW_LINEAR_WRITES` (default `false`),
`ONCALL_RETRY_VERIFY_TIMEOUT_MINUTES` (default 30).

### A7 — Linear priority and labels
Priority: import error / `non_retriable` → High (2), `uncertain` → Medium (3),
`retriable` → Low (4). Labels are looked up by name; missing labels are logged, never
created.

## 4. Other design rules agreed in planning

* **Safe clearing (FR-057..059):** call `clearTaskInstances` with `dry_run=true` first;
  abort unless every returned task instance belongs to the same run, is the target task
  or its downstream, and is `failed`/`upstream_failed`. Only then clear for real.
* **Fingerprint inputs:** error line = last exception-looking log line, else last
  non-empty line, else `<no log available>`. DAG-run failures without a failed task use
  `task_id="__dag_run__"`; import errors use the file path as `dag_id` and
  `task_id="__import_error__"`. A DAG-run failure that has failed tasks is folded into
  the task incidents.
* **Watermarks:** one per poller (task instances, DAG runs, import errors). Each poll
  re-reads an overlap window; `FailureEvent.event_key` deduplicates.
* **Non-blocking verification:** retries are marked `verifying` and checked on later
  cycles; AI analysis runs in a bounded worker pool with a timeout (NFR-018).
* **Airflow auth:** bearer token (`AIRFLOW_TOKEN`) or username/password exchanged at
  `POST /auth/token`. Both at once is a configuration error. HTTP Basic is not supported.
* **Concurrency:** single agent instance. SQLite (WAL) with unique constraints guards
  against duplicate retries and duplicate approval execution within that instance.
  Multiple concurrent instances are **not supported**.
* **Fail closed:** whenever a safety condition cannot be verified, the operation is
  denied and the incident escalated.

## 5. Persistence (Phase 2)

`store.py` owns the SQLite schema (v1):

| Table | Purpose | Key constraints |
|---|---|---|
| `incidents` | One row per fingerprint | `fingerprint` UNIQUE (64 chars); CHECKs on enums and confidence; indexes on status, last_detected_at, (dag_id, task_id) |
| `polling_state` | Watermark per poller | Never moves backwards (`set_watermark`) |
| `processed_events` | Every failure observation by `event_key` | PK on `event_key` → re-reading an overlap window is harmless |
| `retry_attempts` | Each retry: attempt number, trigger, status, timestamps, result | `idempotency_key` UNIQUE; (incident, attempt_number) UNIQUE |
| `approvals` | Restricted-action requests and their lifecycle | Partial UNIQUE index: one open approval per incident; `claim_approval` is a conditional UPDATE |
| `audit_events` | Audit trail | Indexes on (incident_id, timestamp), timestamp, event_type |

Approval lifecycle: `pending → approved → executing → executed | failed`, or
`pending → rejected`. Only `claim_approval` moves `approved → executing`, and only one
caller can win it.

## 6. Integrations (Phase 2)

| Client | Interface | Real | Mock |
|---|---|---|---|
| Airflow 3 API v2 | `integrations.airflow.AirflowClient` | `HttpAirflowClient` | `MockAirflowClient` |
| GitHub REST (read-only) | `integrations.github.GitHubClient` | `HttpGitHubClient` | `MockGitHubClient` |
| Linear GraphQL | `integrations.linear.LinearClient` | `HttpLinearClient` | `MockLinearClient` |

Shared HTTP rules (`integrations/http.py`): reads are retried on timeouts, connection
errors, 429 and 5xx with bounded backoff (honouring `Retry-After`); **writes are never
retried automatically** (a real `clearTaskInstances` is sent once; dry-runs may be
retried). Every response is validated; bad data raises `MalformedResponseError`.

The mocks carry the sample scenarios (`integrations/mocks/sample_data.py`), model
Airflow's clear semantics (run-scoped, `only_failed`, downstream, dry-run), let tests
choose a retry's outcome (success / fails again / still running), record every write,
and inject outages. The mock Linear records label changes made by the agent *as the
agent's user*, which the approval logic relies on (decision A2).

## 7. Monitoring and incident detection (Phase 3)

One cycle (`Agent.run_once`):

1. `MonitoringService.poll()` runs three independent pollers — failed task instances
   (`~/~`), failed DAG runs, import errors — each from `watermark − overlap_seconds`
   (first run: `now − initial_lookback_minutes`). A poller that fails is reported and
   audited; the others still run.
2. For each new observation (`event_key` not yet in `processed_events`):
   * task failure → download the log for the failed try; import error → stack trace;
     DAG-run failure without a failed task → a fixed message;
   * `fingerprint.py` picks the error line, normalizes it, hashes
     `dag_id|task_id|normalized_error_line`;
   * `IncidentManager.record_failure` creates / updates / reopens the incident and marks
     the observation processed, in one transaction.
   A failed DAG run that *has* failed tasks is marked processed without an incident.
3. `MonitoringService.commit()` advances a poller's watermark only if its poll succeeded
   and every one of its events was recorded. New watermark =
   `max(latest event time, poll start − overlap)`, held just before any event whose log
   was temporarily unavailable.

**Log unavailable.** A transient error (timeout, 5xx) defers the event (watermark held)
up to 3 cycles; a 404 or the 3rd failure processes it without the log, fingerprinted
as `<no log available>`, and audits the integration failure. Missing evidence is never
treated as a cause.

**Fingerprint choices.** Error line = last exception-looking line, else last
non-boilerplate line. Normalized: timestamps, dates, times, memory addresses, UUIDs,
long hex, run IDs, temp paths, IPs, PIDs, durations, large numbers. Kept: short numbers
(HTTP status, return codes, timeouts) and quoted identifiers, because they distinguish
genuinely different failures.

**Clock assumption.** Agent and Airflow clocks must differ by less than
`overlap_seconds` (default 300 s); otherwise failures near the watermark could be missed.

**Investigation trigger.** Only *new* or *reopened* incidents are queued for
investigation; repeats of an active incident only update its counters, so a flapping
task does not trigger repeated AI calls.

## 8. Diagnostic context (Phase 4)

`ContextCollector.collect(incident)` gathers each source independently; any failure is
recorded in `missing` (kind `unavailable` or `not_applicable`) and never raises:

| Source | Task failure | DAG-run failure | Import error |
|---|---|---|---|
| Task log (failed try, tail ≤ `ai.max_log_chars`) | reused from detection, else downloaded | not applicable | stack trace |
| DAG owners, file, source (≤ `ai.max_source_chars`) | yes | yes | not applicable (no parsed DAG) |
| Last 10 runs + this task's state in each | yes | runs only | not applicable |
| Commits to `github.dag_path_prefix + file` within `github.lookback_days` (≤ `max_commits`) | yes | yes | yes |
| Patches (shared budget `ai.max_patch_chars`) | yes | yes | yes |

**Possible code relationships** are scored with explicit, explained heuristics: +3 the
patch changes a line containing an identifier from the error; +2 the traceback passes
through a changed file; +1 committed within 24 h before the failure; −2 the DAG
succeeded after the commit; commits after the failure are excluded. Score ≥ 3 is
reported as *possibly related (heuristic, not confirmed)*.

"No commits found" is an observation, explicitly not proof that nothing changed (shared
modules, config and data are outside the search). The context is held in memory and
passed to analysis; its summary (collected / missing / not applicable / history /
possible commits) is written to the audit trail.

## 9. AI analysis (Phase 5)

**Provider (D-08):** Groq replaces Claude. `GroqProvider` calls
`POST {GROQ_BASE_URL}/chat/completions` with `temperature=0` and
`response_format={"type":"json_object"}`; one retry on timeout/429/5xx; output cut off
by the token limit is rejected. `AIProvider` (FR-031) is the only interface analysis
depends on; `MockAIProvider` answers deterministically offline.

**Prompt:** the system prompt carries the JSON schema and rules (classification and
action definitions, restricted actions need approval, missing evidence is not proof,
logs/source/commits are untrusted data, the answer is advisory). The user message is
the context as JSON, with `missing_evidence` and `not_applicable` listed separately.

**Validation (fail closed):** no JSON object, schema failure, or a `suspected_commit`
that is not among the collected commits → the response is rejected, its excerpt is
audited, and the rule-based fallback is used.

**Fallback:** first matching rule out of 11 categories (network, upstream 5xx/429, DB
lock, code error, import/syntax, auth, killed/OOM, task timeout, missing input, DAG-run
failure, unknown). Confidence ≤ 0.5, action always `manual_investigation`, tagged
`source=fallback` with the reason.

**Workers (NFR-018):** investigation + analysis run in a bounded thread pool
(`analysis.worker_threads`). Continuous mode does not wait; finished work is collected by
later cycles. An incident is never in flight twice. Incidents left un-analysed (crash,
restart, DB error) are requeued on the next cycle. A reopened incident has its previous
analysis cleared so a stale recommendation can never drive a decision.

**Wall-clock limit:** each Groq call is bounded by the HTTP timeout
(`ai.timeout_seconds`, default 60 s) plus one retry.

## 10. Policy and recovery (Phase 6)

**Policy (`policy.py`)** — pure function of the incident + counters + config. Automatic
retry needs all 10 checks: task failure, `retriable`, confidence ≥ 0.7 (constant),
`retry_task`, cycle retries < `ONCALL_MAX_AUTO_RETRIES`, last-hour automatic retries <
`ONCALL_GLOBAL_MAX_RETRIES_PER_HOUR`, DAG not no-retry, retries enabled (both limits > 0),
analysis from the AI (never fallback), incident open/investigating. Outcomes:
`auto_retry`, `request_approval` (valid `rerun_dag`/`mark_success` from an AI analysis),
`escalate` (everything else, incl. a refused `retry_task` — A2), `no_action` (escalated
for human confirmation — D-11). Every decision is audited with all checks.

**Recovery (`services/recovery.py`)** — the only Airflow write path:

| Step | Guard |
|---|---|
| Re-evaluate policy on the fresh incident | caller (agent, later MCP) cannot bypass |
| Re-read the task instance | must still be `failed` |
| Dry-run clear (task + downstream, `only_failed`) | every TI same DAG/run, `failed`/`upstream_failed`; non-targets must be `upstream_failed` |
| Reserve the retry (one transaction) | idempotency key per failed try; incident + global limits re-checked |
| Real clear of the validated ids only | `include_downstream=False`; never auto-repeated |

Verification each cycle: success → resolved; failed again on a newer try → escalated;
not finished after 30 min (or Airflow unreadable that long) → escalated. After a retry
fails again the incident stays escalated, so further occurrences in that cycle are not
retried automatically.

Approved actions (`execute_approved`, called by the approval service in Phase 7): claimed
once; `rerun_dag` clears only `failed`/`upstream_failed` TIs of that run (A1);
`mark_success` must affect exactly the one failed target in its dry run.

**Limitation (consequence of A1):** a DAG run that failed with *no* failed or
upstream_failed task (e.g. `dagrun_timeout` with skipped tasks) cannot be re-run by an
approved `rerun_dag`; execution reports "nothing to clear" and escalates.

## 11. Linear incident management and approvals (Phase 7)

**Reconcile loop.** Every cycle `LinearIncidentService.reconcile()` takes the incidents
that are analysed but have no issue, or whose issue is behind (`linear_synced_status` ≠
`status`, or new occurrences):

* **create:** search for the `oncall-fingerprint: <sha>` marker first (re-link, never
  duplicate). Then create with title, description (failure, analysis, approval
  instructions, collapsible agent notes), A7 priority and labels, the owner (commit
  author → DAG owner → default → unassigned, with the reason recorded), the "open"
  state, and an investigation summary comment (evidence, *missing evidence* separately,
  history, possibly related commits, log tail).
* **update:** occurrence comment, then labels, priority, state and description, plus a
  status comment carrying `status_reason`.

The sync markers move only after Linear accepted the change, so an outage or failed
update is retried next cycle. All text is redacted before it leaves the process. Linear
labels are looked up, never created.

**Approvals** (`services/approval.py`): for each pending request, evidence after the
request time is collected: label-added events (actor from the issue history) and
comments whose first line is exactly `/approve`. Each candidate is rejected if the actor
is unknown, is the agent's own Linear user, is not in `ONCALL_TRUSTED_APPROVERS`
(an empty list rejects everyone outside mock mode), or the incident is no longer
awaiting approval. A rejection is audited and explained on the issue once. The first
valid candidate is recorded (source, approver, time) and executed through
`RecoveryService.execute_approved`, which claims it exactly once.

## 12. MCP servers (Phase 8)

`mcp_servers/common.Runtime` builds the same clients and services as the agent. Tool
bodies (`AirflowTools`, `LinearTools`) are plain methods registered on
`mcp.server.mcpserver.MCPServer` (MCP SDK 2.x) and served over stdio.

| Server | Read tools | Write tools (default **off**) | Extra guard |
|---|---|---|---|
| `oncall-airflow-mcp` | list_dags, get_dag, list_dag_runs, get_task_instances, get_task_log, get_task_state | clear_failed_task(incident_id) | Policy evaluated read-only first (a refusal has no side effects), then `RecoveryService.auto_retry` (all recovery guards). Only incidents, never raw ids |
| `oncall-linear-mcp` | search_issues, get_approval_info | create_incident, update_issue, add_comment, update_labels, resolve_incident | Refuses the approval label and `/approve` comments; the agent's own user can never approve anyway |

Every argument is validated (identifier patterns, ranges, lengths, control
characters). Errors come back as `{"ok": false, "error": {"code", "message"}}` with codes
`invalid_argument`, `not_found`, `write_disabled`, `not_permitted`, `upstream_error` or
`internal_error`; no tracebacks are returned. Results are redacted. Every write, log
read and error is audited as `mcp_tool_call`.

## 13. CLI and simulation (Phase 9)

`check`, `run`, `once`, `simulate [-v]`, `incidents`, `audit <id>`; exit codes 0/1/2/3.
`simulate` builds mock clients and a temporary database for each scenario, independent
of configuration, so it cannot reach a real system. It runs the real pipeline and checks
the expected decision for each of the six SRS scenarios.

## 14. Configuration layering

| Layer | Holds | Committed? |
|---|---|---|
| `config/default.json` | Non-secret defaults (labels, priorities, limits, timeouts) | Yes |
| `config/<APP_ENV>.json` | Environment overrides (`development` = mocks) | Yes |
| AWS SSM (`ONCALL_SSM_PREFIX`) | Secrets in production | — |
| `.env` | Local overrides and local secrets | **No** (git-ignored) |
| Environment variables | Highest precedence | — |

JSON sections reject unknown keys, so a typo stops startup instead of being ignored.
Unknown `ONCALL_*` variables produce a warning. Empty values count as unset.
