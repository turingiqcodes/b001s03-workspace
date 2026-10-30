# PR review report format

```markdown
## PR review: <title or branch> (<base>…<head>)

**Verdict:** Request changes | Approve with comments | Approve
<one or two sentences: what the PR does and the main risk>

| # | Severity | Area | Location | Finding |
|---|----------|------|----------|---------|
| 1 | blocker  | data-pipeline | dags/x.py:120 | Re-run duplicates rows in public.sales |

### 1. <title>  ·  blocker · CONFIRMED
`dags/x.py:120`
**What happens:** <concrete scenario>
**Fix:** <minimal change, code snippet if short>

...

<details><summary>Rejected during verification (N)</summary>

- <title> — <reason>
</details>

Reviewers run: correctness, security, tests/maintainability[, data-pipeline]
```

Rules:
- Order by severity: blocker → major → minor → nit.
- Every finding has a `file:line` and a concrete scenario. No "consider", "might want to" filler.
- If there are no findings, say so in one line and list which reviewers ran.
- When posting to GitHub, use the same text; inline comments carry the per-finding section, the review body carries the verdict and the table.
