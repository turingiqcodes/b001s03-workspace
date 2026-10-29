---
name: app-bug-detection
description: Find correctness bugs in the Digital Library app (FastAPI backend in backend/, vanilla JS frontend in frontend/). Use when asked to hunt for bugs, review a change for correctness, or check whether an endpoint behaves as the README says.
---

# Bug detection — Digital Library app

Look for code that produces a wrong result, crashes, or contradicts the documented behaviour in
`README.md`. Report only bugs you can tie to a concrete input; style belongs to `app-code-quality`.

## Scope

Default: every file in `backend/` and `frontend/`. If the user names files, a diff, or a branch,
review only that (`git diff main...HEAD -- .` for the branch).

## What to check

**Backend (`backend/main.py`, `auth.py`, `db.py`, `models.py`)**
- Every route: does the role check (`Depends(admin)`, `Depends(author)`, `current_user` + `on_title`)
  match the README's API table? An author must never reach another author's book.
- `db.write_pubs` vs `db.write_app`: pubs tables must go through `write_pubs` (practice mode);
  app tables (`app_users`, `app_sessions`, `app_title_removals`) through `write_app`. A statement that
  mixes them in one `write_pubs` call is silently rolled back in practice mode — check the caller
  expects that.
- Check-then-insert races (SELECT to see if a row exists, then INSERT): does the unique constraint
  still produce a clean 409 via `DB_MESSAGES`, or a 500?
- `None` handling: `price`, `royaltyper`, `au_id` (admin has no `au_id`), optional body (`LogoutIn`).
- Pydantic models: `extra="forbid"`, the `_Form` blank-to-None validator, and field validators that
  read `info.data` (a field that failed validation is missing from `info.data`).
- Pagination: `page`/`page_size` bounds, OFFSET maths, totals query using the same `WHERE` as rows.
- Cookies: `write_account_cookies` max_age, the `dl_others` token list, `MAX_ACCOUNTS` trimming.
- `TYPE_LABEL`/`types` columns are `char(12)` in pubs: compare with `trim()` where needed.

**Frontend (`frontend/*.js`)**
- `api()` callers that ignore `ApiError.errors` or never handle a 401/403/409.
- Hash routing in `app.js`: unknown routes, stale `query` after navigation.
- Number/date formatting helpers in `lib.js` on `null`, empty string, or a string number.

## How to verify

1. Read the code path end to end (route → model → SQL → response → the JS that renders it).
2. Where possible, prove it: write a throwaway test in the scratchpad or run one against the API with
   `TestClient` (see `tests/test_api.py` for the `session_for` fixture pattern). Tests run against the
   real database in practice mode: never set `DL_ALLOW_WRITES=1` while probing.
3. Drop anything you cannot reproduce or explain with a concrete input.

## Report

A table: **Severity** (High/Medium/Low) · **File:line** · **Bug** · **Input that triggers it** ·
**Fix**. Highest severity first. Say plainly if nothing was found.
