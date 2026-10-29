---
name: app-full-review
description: Run all five Digital Library app reviews in one pass — bug detection, security, performance, code quality, and tests — and produce one combined report with prioritised recommendations. Use when asked for a full review, audit or health check of the app folder.
---

# Full review — Digital Library app

Run these skills in order, each over the same scope (the whole app, or the diff/files the user named):

1. `app-bug-detection`
2. `app-security-review`
3. `app-performance-review`
4. `app-code-quality`
5. `app-test-recommendations`

Read the code once and reuse what you learned across the five passes. A finding belongs to one
section only (a security bug goes under Security, not also under Bugs).

## Combined report

1. **Summary** — one paragraph and a count of findings per section and severity.
2. One section per skill, using that skill's report format.
3. **Top recommendations** — the 5–10 changes to make first, ordered by risk reduced per effort.
4. **Test result** — the exact final line of the pytest run.

Do not change application code unless the user asks; adding tests in step 5 is allowed.
