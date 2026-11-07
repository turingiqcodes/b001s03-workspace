# Troubleshooting — Airflow Oncall Agent

Start with `oncall-agent check`. It validates the configuration and tests every
integration without changing anything. Then look at an incident's history with
`oncall-agent audit <id> -v`.

## Start-up

| Symptom | Cause | Fix |
|---|---|---|
| `Configuration error — the agent will not start` (exit 3) | A required setting is missing or invalid | The message lists every problem. In production all credentials are required and mocks are refused |
| `mock integrations are not allowed in production` | `ONCALL_INTEGRATION_MODE=mock` (or the mock AI) with `APP_ENV=production` | Use `real` and `AI_PROVIDER=groq` |
| `set either AIRFLOW_TOKEN or AIRFLOW_USERNAME/AIRFLOW_PASSWORD, not both` | Both auth methods configured | Keep exactly one |
| `Cannot load secrets from SSM` | Missing IAM permission, wrong region, or wrong prefix | Check the instance role ([deploy/iam-policy-ssm.json](../deploy/iam-policy-ssm.json)), `AWS_REGION` and `ONCALL_SSM_PREFIX` (it must end with `/`) |
| `Unknown variable ONCALL_… is ignored` (warning) | Typo in a variable name | Fix the name. See `.env.example` |
| `Extra inputs are not permitted` for a `config/*.json` key | Typo in the JSON config | Fix the key. Unknown keys are rejected on purpose |
| `Database schema vN is newer than this agent supports` | An older agent was started on a newer database | Run the newer version, or restore a backup |

## Connectivity (`check` shows FAIL)

| Integration | Message | Fix |
|---|---|---|
| airflow | `HTTP 401` | Token expired or wrong. With username/password, check the user exists in Airflow's auth manager |
| airflow | `HTTP 404` on `/api/v2/version` | Base URL wrong (do not include `/api/v2`), or Airflow is older than 3.x |
| airflow | `connection failed` / `timed out` | Network, DNS or proxy. The agent retries reads and keeps the watermark while Airflow is down |
| github | `HTTP 403 Resource not accessible` | Token lacks "Contents: read" on the repository |
| linear | `Team … not found` | Wrong `LINEAR_TEAM_ID`, or the API key's user is not a team member |
| linear | `missing in Linear (create them manually)` (warning) | Create the listed labels. The agent never creates them |
| ai | `model '…' is not available to this Groq API key` | Fix `GROQ_MODEL` (see `GET /openai/v1/models`) |

## Behaviour

| Symptom | Explanation |
|---|---|
| A retriable failure was **escalated**, not retried | Run `oncall-agent audit <id> -v` and read the `policy_decision` event. It lists all 10 checks and which failed (confidence < 0.7, fallback analysis, no-retry DAG, a limit reached, retries disabled…) |
| Every analysis says "Rule-based fallback" | The AI provider is failing. See the `integration_failure` events and `check`. Fallback analyses never trigger retries |
| `unsafe clear refused` | The dry-run would have touched a successful or unrelated task instance, so the agent cleared nothing. Investigate manually |
| A retry was escalated after 30 min | The task did not finish within `ONCALL_RETRY_VERIFY_TIMEOUT_MINUTES`. Check the Airflow workers and queue |
| An approval was **ignored** | The issue has a comment explaining why: not in `ONCALL_TRUSTED_APPROVERS`, made by the agent's own Linear user (including through MCP), dated before the request, or the incident was no longer awaiting approval. Only the label `oncall:approved` or a comment whose **first line** is exactly `/approve` counts |
| An approved `rerun_dag` failed with "nothing to clear" | Decision A1: `rerun_dag` clears only failed/upstream_failed tasks. A DAG run that failed without such tasks (for example a `dagrun_timeout` with skipped tasks) cannot be re-run this way. Re-run it in Airflow |
| A Linear issue is not updated | Linear was unavailable. The agent retries every cycle. Look for `integration_failure` events with action `linear.sync` |
| Two incidents for what looks like the same problem | Different normalized error lines, task IDs or DAGs give different fingerprints. Compare `fingerprint_input` in the incident (`oncall-agent incidents --json`). A failure whose log was unavailable is fingerprinted as `<no log available>` |
| `Approval … could not be checked: Issue … not found` in **mock** mode across commands | The mock Linear lives in memory per process, but the database remembers issue IDs from an earlier run. Delete the development database (`data/oncall.db`) |

## MCP servers

| Symptom | Fix |
|---|---|
| `write_disabled` | Expected by default. Set `ONCALL_MCP_ALLOW_AIRFLOW_WRITES` / `ONCALL_MCP_ALLOW_LINEAR_WRITES=true` only where needed |
| `not_permitted: the policy does not allow an automatic retry` | The MCP tool follows the same policy as the agent. The incident is not eligible |
| `not_permitted: approval commands cannot be posted through MCP` | By design. A trusted human must approve in Linear |
| The client shows protocol errors | Something wrote to stdout. The servers log to stderr only. Check wrappers or shell profiles |

## Collecting information for a bug report

```bash
oncall-agent check
oncall-agent incidents --json --limit 20 > incidents.json
oncall-agent audit <id> -v > audit.txt
journalctl -u oncall-agent --since "1 hour ago" > agent.log
```

All of these outputs are redacted, but review them before sharing.
