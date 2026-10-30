---
name: pr-review
description: Review a pull request (GitHub PR number, branch, or the current branch vs main) with parallel specialist reviewer agents — correctness, security, data-pipeline (Airflow/SQL/Terraform), tests & maintainability — then verify findings and report them. Optionally post the review to GitHub with --post. Use when the user asks to review a PR, review their changes, or check a branch before merge.
argument-hint: "[PR number | branch] [--post] [--fix]"
---

# PR review

Arguments: `$ARGUMENTS`

## 1. Resolve the target
- **PR number** (e.g. `42`): load `mcp__github__get_pull_request` (title, body, base, head), `mcp__github__list_pull_request_files`, and `mcp__github__get_pull_request_diff`. Load the github tools with ToolSearch first if they are deferred. The repo owner/name comes from `git remote get-url origin`. If the head branch exists locally, prefer `git diff origin/<base>...origin/<head>` after `git fetch`.
- **Branch name**: base = `main`, head = that branch.
- **Nothing given**: base = `main`, head = `HEAD` (include uncommitted changes with `git diff main` if the working tree is dirty, and say so).

Write the unified diff to a temp file (`mktemp -d`, then `<dir>/pr.diff`) so all reviewers read the same diff, and collect: changed file list, `git log --oneline <base>..<head>`, PR title/body if any.

If the diff is empty, stop and say so. If it is over ~3000 changed lines, tell the user and review the highest-risk files first (DAGs, SQL, Terraform, auth/config code) instead of skimming everything.

## 2. Pick reviewers
Always run `pr-correctness-reviewer`, `pr-security-reviewer` and `pr-tests-maintainability-reviewer`.
Also run `pr-data-pipeline-reviewer` when any changed path matches `dags/`, `*.sql`, `*.tf`, `*.tfvars` or `*.tftpl`.

Launch them **in parallel in a single message** with the Agent tool. Give each one the same brief:
- the diff file path, base and head refs, and the changed-file list
- the PR title/description and commit list
- "Report in your output format; return NO_FINDINGS if nothing survives your own proof step."

## 3. Verify
Collect all findings. If there are any, launch one `pr-finding-verifier` with all of them (verbatim) plus the diff path and refs. Keep only `CONFIRMED` and `PLAUSIBLE`. Drop `nit`s unless the user asked for them.

## 4. Report
Use the format in [references/report-format.md](references/report-format.md). Show it in the terminal. Lead with the verdict: **Approve**, **Approve with comments**, or **Request changes** (any confirmed `blocker` or `major` → Request changes).

## 5. Optional actions (only when the flag was passed)
- `--post`: post the review on the PR with the github MCP tools. Show the user what will be posted and get their confirmation first, because this publishes to GitHub. Create a pending review with `mcp__github__create_pull_request_review` (inline comments on `path` + `line` for each finding whose line is in the diff, other findings go in the body), then `mcp__github__submit_pull_request_review` with event `COMMENT` or `REQUEST_CHANGES`. **Never use `APPROVE`** (a human approves). Never merge.
- `--fix`: after the report, apply fixes for `CONFIRMED` findings only, one at a time, in the working tree. Do not commit. List what changed and which findings were left for the author.

Never post or fix without the matching flag. If the user asks for that afterwards, do it then.
