# Changelog

All notable changes to the Airflow Oncall Agent are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-11-05 (unreleased)

First complete version, built to the SRS baseline v1.0 in ten phases. Requirement-level
status is in [docs/requirements-traceability.md](docs/requirements-traceability.md).

### Added

- **Foundation:** packaging (`pyproject.toml`); layered configuration (environment,
  `.env`, AWS SSM, JSON); production fail-closed validation; text/JSON logging with
  secret redaction; domain models; `oncall-agent check`.
- **Persistence:** SQLite store (WAL, transactions, migrations v1–v3) with
  duplicate-execution guards; audit trail in SQLite and `logs/audit.jsonl`.
- **Integrations:** Airflow 3.x REST API v2 client (bearer token or username/password →
  JWT); read-only GitHub REST client; Linear GraphQL client; offline mocks with sample
  failures and fault injection.
- **Monitoring:** pollers for failed task instances, failed DAG runs and import errors,
  with persistent watermarks and an overlap window; error normalization; SHA-256
  fingerprints; incident deduplication and reopen.
- **Investigation:** task logs, DAG source, last 10 runs, commits and patches, explained
  code-change heuristics, explicit missing evidence.
- **AI analysis:** `AIProvider` interface; **Groq** provider (OpenAI-compatible, JSON
  mode); strict response validation; rule-based fallback (confidence ≤ 0.5, never
  retries); bounded analysis worker pool.
- **Policy and recovery:** deterministic 10-check policy; dry-run validated clearing that
  never touches successful or unrelated task instances; atomic per-incident and hourly
  limits; retry verification and escalation; approved `rerun_dag` / `mark_success`
  executed exactly once.
- **Linear:** issue creation and reconcile loop, labels and priority, ownership resolution,
  investigation summaries, status and occurrence comments, approvals via
  `oncall:approved` or `/approve` with trusted-approver validation.
- **MCP:** `oncall-airflow-mcp` and `oncall-linear-mcp` (stdio) with validated, audited,
  write-gated tools.
- **CLI:** `run`, `once`, `simulate`, `incidents`, `audit`.
- **Documentation and deployment:** architecture, traceability, acceptance results,
  deployment and troubleshooting guides; systemd unit, IAM policy, MCP client example.
- **Tests:** 577 automated tests (unit, integration, end-to-end); AT-001 to AT-024 pass;
  6/6 simulation scenarios pass.

### Changed (deviations from the SRS, see the traceability document)

- **D-08:** Groq replaces Claude as the AI provider (`GROQ_*` variables instead of
  `CLAUDE_*`).
- **D-11:** a `no_action` recommendation escalates for human confirmation instead of
  closing the incident.
- Other recorded deviations: D-01 to D-07, D-09, D-10, D-12 to D-14.

### Known limitations

See [README.md#limitations](README.md#limitations).
