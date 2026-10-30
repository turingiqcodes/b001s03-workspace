# pr-reviewer prompt: status and next steps

Prompt: `pr-reviewer.md` (this folder). It is parked here, so Claude Code does not load it as an agent.
To activate it, move it back: `mv .claude/PR_prompt/pr-reviewer.md .claude/agents/`

## Test run 1 (2026-10-30): branch b001s03-full-pubs-pipeline vs main

The PR had 136 files and +30,116/−429 lines across 2 commits: "B001S03: Load all 11 pubs tables…" and "just push".
The agent's verdict was **Request changes**. The run took about 2 min and 72k tokens, and produced no false positives.

| # | Severity | Finding | Checked |
|---|---|---|---|
| 1 | major | Six projects mixed into one PR; the DAG commit touches 1 file, "just push" adds 135 | ✅ real |
| 2 | major | Committed artifacts: 27 MB terraform zip, 10.6 MB ruff wheel, `tfplan`, `.pyc` files, `test_report.txt`, `.vscode/`. `.gitignore` has `*.tfplan`, which doesn't match a file named `tfplan` | ✅ real |
| 3 | major | The stage→public data checks run after `insert_into_public` has already committed, so a failure can't roll it back. The fixed `threshold` (`target == src + threshold`) fails on updates and on later loads (`dags/b001s03_pipeline_dag.py:197-206`, `:327-351`) | ✅ confirmed |
| 4 | major (low conf.) | The embedded CSV fallback was removed; the Airflow host may lack S3 credentials or boto3 (`:177-194`) | ⚠️ plausible |
| 5 | major (low conf.) | `jobs.job_id` is copied from the stage table's default value, so IDs may collide or drift (`:67-72`) | ⚠️ the setting is real; collision depends on stage DDL not in the repo |
| 6 | minor | No tests for `qc_passes`, `public_insert_sql` or control-table filtering | ✅ real |

## Pending prompt fixes (not applied yet)

1. **Strict severity rule.** Finding 2 was rated major even though the prompt says committed files over 10 MB or secrets are a blocker. Reword the rule so it can't be argued around.
2. **Main-change focus.** For a mixed PR, work out the main change from the title and commits and deep-review only that. Other projects get only a secrets/artifacts scan. In run 1 it spent budget on Terraform and `auth.py`.
3. **"To confirm:" line.** Every low-confidence finding must name the one fact or command that would confirm or rule it out.
4. **Shorter findings.** Cap "What happens" at about 5 lines, and list the files to remove as a compact table. Finding 2 ran to about 25 lines.
