---
name: pr-preflight
description: Author-side check before opening or updating a pull request — scans the branch diff for secrets and committed artifacts, checks Python syntax and DAG self-containment, runs tests for touched projects, checks commit messages, and drafts the PR description. Use when the user says they're about to open a PR, asks "is this ready for PR", or asks to draft a PR description.
argument-hint: "[base branch, default main]"
---

# PR preflight

Base branch: `$ARGUMENTS` (default `main`). Compare with `git diff <base>...HEAD` and `git log <base>..HEAD`.

Run these checks and report each as ✅ / ⚠️ / ❌ with the evidence:

1. **Working tree**: uncommitted or untracked files that look like they belong in the PR.
2. **Forbidden files in the diff**: `.env` (not `.env.example`), `*.tfstate*`, `tfplan`, real `terraform.tfvars`, `*.pem`, `__pycache__/`, `.ipynb_checkpoints/`, `*.zip` or any file over 1 MB.
3. **Secrets**: run `python3 .claude/hooks/scan_secrets.py` on each changed file's added lines, by piping `{"tool_name":"preflight","tool_input":{"content": <added lines>}}` to it. Exit code 2 = finding.
4. **Python syntax**: compile every changed `.py` file (`python3 -c "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')" <file>`).
5. **DAG rules** (changed files under `dags/`): no imports/reads of sibling files without an embedded fallback; credentials from an Airflow Connection; static `start_date`. Also, if Airflow is installed, `python3 -c "from airflow.models import DagBag; b=DagBag('dags', include_examples=False); print(b.import_errors)"`.
6. **Tests**: for changes under `github-mcp-server/`, run `cd github-mcp-server && .venv/bin/python -m pytest -q`. For other projects with a `tests/` folder, run their tests. Report failures with output; don't hide them.
7. **Commits**: every message should be `B001S03: <imperative summary>`. List the ones that aren't (e.g. "just push") and suggest a reword, but don't rewrite history unless the user asks.

Then draft a PR description:

```markdown
## Summary
<what changed and why, 2–4 bullets>

## How it was tested
<commands run and results>

## Risk / rollout
<data migrations, re-run safety, infra changes, anything reviewers should look at first>
```

Finish with the next step: fix the ❌ items, or run `/pr-review` for a full review. Do not push or open the PR unless the user asks.
