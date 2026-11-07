# Deployment — Airflow Oncall Agent v1.0

This guide covers a production deployment on Linux (NFR-037). Example files are in
[`deploy/`](../deploy).

## Assumptions (confirm before going live — SRS Part 19)

| Assumption | Why it matters | If different |
|---|---|---|
| A systemd-based Linux host (VM or EC2) with Python ≥ 3.10 | Process supervision below uses systemd | Any supervisor that restarts on failure and sends SIGTERM works; see "Process supervision" |
| **One** agent instance per SQLite database | SQLite + in-process locks guarantee single execution only within one process | Do not run two agents against the same database. Multi-instance needs a shared database and distributed locking (not in v1) |
| AWS SSM Parameter Store for secrets, with an instance role | NFR-008 | Use environment variables injected by your secret manager instead (same names) |
| The agent can reach Airflow (API v2), `api.github.com`, `api.linear.app`, `api.groq.com` over HTTPS | Integrations | Set `AIRFLOW_BASE_URL` / `GROQ_BASE_URL` for proxies |
| Airflow 3.x with JWT auth at `POST /auth/token` | FR-001/003 | Verify endpoints against your Airflow version (open item) |

## 1. Linux setup

```bash
sudo useradd --system --home /opt/airflow-oncall-agent --shell /usr/sbin/nologin oncall
sudo mkdir -p /opt/airflow-oncall-agent /var/lib/oncall-agent /var/log/oncall-agent /etc/oncall-agent
sudo chown oncall:oncall /var/lib/oncall-agent /var/log/oncall-agent
sudo chmod 750 /var/lib/oncall-agent /var/log/oncall-agent
```

## 2. Python environment and dependencies

The project lives in the `airflow-oncall-agent/` folder of the repository; install that
folder as `/opt/airflow-oncall-agent` (the paths in `deploy/` assume this):

```bash
git clone <repo> /tmp/src                                          # or unpack a release
sudo cp -r /tmp/src/airflow-oncall-agent/. /opt/airflow-oncall-agent/
sudo chown -R oncall:oncall /opt/airflow-oncall-agent
cd /opt/airflow-oncall-agent
sudo -u oncall python3 -m venv .venv
sudo -u oncall .venv/bin/pip install --upgrade pip
sudo -u oncall .venv/bin/pip install ".[aws]"                     # boto3 for SSM
```

With a regular (non-editable) install the JSON config is not inside the package, so
`ONCALL_CONFIG_DIR=/opt/airflow-oncall-agent/config` (already in the example env file)
tells the agent where it is. Pin versions for reproducible deploys (`pip freeze > constraints.txt` on a tested build,
then `pip install -c constraints.txt ".[aws]"`).

## 3. Configuration

* Non-secret settings: copy [`deploy/oncall-agent.env.example`](../deploy/oncall-agent.env.example)
  to `/etc/oncall-agent/oncall-agent.env` (`chmod 640`, owner `root:oncall`) and fill it in.
* Tunables that are not environment variables (labels, priorities, Linear state names,
  no-retry DAGs, owner mapping) live in `config/default.json` and `config/production.json`.
  Set `policy.no_retry_dags` and `ownership.user_map` in `config/production.json`.
* `APP_ENV=production` makes the agent **refuse to start** (exit 3) with mock
  integrations, the mock AI provider, or any missing credential.
* Set `ONCALL_TRUSTED_APPROVERS`. If it is empty, every approval is rejected.

Linear workflow state names default to `Todo`, `In Progress` and `Done`
(`linear.states`). Create these labels in the Linear team. The agent never creates them,
and `check` lists any that are missing: `oncall`, `oncall:approved`, `oncall:retriable`,
`oncall:non-retriable`, `oncall:uncertain`, `oncall:import-error`,
`oncall:awaiting-approval`, `oncall:escalated`.

## 4. Secrets in AWS SSM (NFR-008)

Store each secret as a **SecureString** under the prefix:

```bash
P=/oncall-agent/prod/
aws ssm put-parameter --type SecureString --name ${P}AIRFLOW_TOKEN   --value '…'   # or AIRFLOW_PASSWORD (+ AIRFLOW_USERNAME in the env file)
aws ssm put-parameter --type SecureString --name ${P}GITHUB_TOKEN    --value '…'
aws ssm put-parameter --type SecureString --name ${P}LINEAR_API_KEY  --value '…'
aws ssm put-parameter --type SecureString --name ${P}GROQ_API_KEY    --value '…'
```

Give the host's instance role [`deploy/iam-policy-ssm.json`](../deploy/iam-policy-ssm.json):
`ssm:GetParameters` on that prefix only, plus `kms:Decrypt` through SSM. Secrets are read
once at start-up. A real environment variable with the same name overrides SSM. If SSM
cannot be read, the agent does not start (fail closed). To rotate a secret, update the
parameter and restart the service.

### Least privilege (NFR-013)

| Integration | Minimum access |
|---|---|
| Airflow | A dedicated user/role that can read DAGs, DAG runs, task instances, logs and import errors, and **edit task instances** (needed only to clear failed tasks / set an approved `mark_success`). No admin, no connections/variables access |
| GitHub | Fine-grained token, **read-only "Contents"** on the DAG repository only |
| Linear | API key of a dedicated "Oncall Agent" user that belongs to the incident team. Its own label changes and comments are never accepted as approvals |
| Groq | A project API key used only by the agent |
| AWS | The IAM policy above |

## 5. Database initialisation and migration

There is no separate step. The first command that opens the database creates it, and
pending schema migrations (currently v1 to v3) are applied in one transaction. Run
`check` once after install or upgrade:

```bash
sudo -u oncall env $(grep -v '^#' /etc/oncall-agent/oncall-agent.env | xargs) .venv/bin/oncall-agent check
```

A database written by a **newer** agent version is refused, not downgraded.

## 6. Logging

| Output | Where | Format |
|---|---|---|
| Application log | stderr → journald | `ONCALL_LOG_FORMAT` (`json` recommended) |
| Application log file | `$ONCALL_LOG_DIR/agent.log` (rotated, 10 MB × 5) | JSON |
| Audit trail | `audit_events` table + `$ONCALL_LOG_DIR/audit.jsonl` | JSON lines |

Secrets are redacted everywhere. `audit.jsonl` is not rotated by the agent. Add
logrotate:

```
/var/log/oncall-agent/audit.jsonl { weekly rotate 26 compress copytruncate missingok }
```

## 7. Running and process supervision

```bash
sudo cp deploy/oncall-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oncall-agent
journalctl -u oncall-agent -f
```

The unit runs `check` before starting, restarts on failure after 30 s, and **does not
restart on exit code 3** (configuration errors need a human). Without systemd, use any
supervisor that restarts on non-zero exit codes other than 3 and stops with SIGTERM.

## 8. Restart behaviour

Everything needed to continue is in SQLite:

* **Watermarks:** monitoring resumes from the last successful watermark minus the overlap
  window. Nothing is lost, and nothing is processed twice.
* **Incidents without analysis or decision** are picked up again on the first cycle.
* **Retries in progress** are verified by the restarted agent. The 30-minute timeout
  counts from the original start.
* **Approvals:** a pending request is still checked. An approved action executes at most
  once, even across restarts.

On SIGTERM the agent finishes the current cycle and waits for running analyses. Each
analysis is bounded by `ai.timeout_seconds`. The unit allows 180 s.

## 9. Health checks

* `oncall-agent check` (exit 0 / 1 / 3) checks the configuration, the database, Airflow,
  GitHub, Linear, the AI provider and the labels. Run it from your monitoring every few
  minutes. It is read-only.
* **Liveness:** `systemctl is-active oncall-agent`.
* **Progress:** every cycle writes a `poll_completed` audit event. Alert if none has been
  written for 3 × `POLL_INTERVAL_SECONDS`:
  `sqlite3 /var/lib/oncall-agent/oncall.db "select max(timestamp) from audit_events where event_type='poll_completed'"`.
* **Failures to watch:** `poll_failed`, `integration_failure`, `retry_timed_out` and
  `incident_escalated` events.

## 10. Backup, recovery and retention

* **Online backup** (safe while running, WAL-aware):
  `sqlite3 /var/lib/oncall-agent/oncall.db ".backup '/backup/oncall-$(date +%F).db'"`
  Schedule it daily and keep copies off the host.
* **Restore:** stop the service, replace the database file (and remove any `-wal`/`-shm`
  files), then start it. Issues already in Linear are re-linked by their fingerprint
  marker, not duplicated. Approvals executed after the backup *could* be seen as pending
  again, so check `oncall-agent incidents --status awaiting_approval` after a restore.
* **Losing the database** is survivable. Incidents are re-detected within the initial
  look-back window, and Linear issues are re-linked. Older history and audit events are
  lost.
* **Retention:** v1 never deletes rows. SQLite handles the expected volume (thousands of
  incidents) easily. Archive or prune old `audit_events` / `processed_events` manually if
  needed. This is an open item (SRS Part 19).

## 11. MCP servers

Both servers run on demand over stdio and use the same configuration. See
[`deploy/mcp-clients.example.json`](../deploy/mcp-clients.example.json). Writes stay off
unless `ONCALL_MCP_ALLOW_AIRFLOW_WRITES` / `ONCALL_MCP_ALLOW_LINEAR_WRITES` are `true`.
Even then, the Airflow write tool is limited by the policy engine, and the Linear tools
can never approve anything. They share the agent's SQLite database, so run them on the
same host.

## 12. Upgrades

Stop the service, back up the database, install the new version, run `check` (this
applies migrations), then start the service.

See [troubleshooting.md](troubleshooting.md) for common problems.
