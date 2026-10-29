---
name: app-test-recommendations
description: Assess and extend the Digital Library app's tests — run the suite, map coverage to routes, find untested behaviour, and write new pytest cases in tests/test_api.py style. Use when asked for test cases, test coverage, or testing recommendations for the app folder.
---

# Test cases & recommendations — Digital Library app

## 1. Run what exists

```bash
cd app && .venv/bin/python -m pytest -q
```

Tests hit the real pubs database in practice mode. The module fixture `pubs_tables_unchanged`
asserts row counts of the 11 pubs tables are identical before and after — keep it that way.
If the database is unreachable the suite skips; say so instead of reporting a pass.

## 2. Map coverage

List every route (`grep -n "@app\." backend/main.py`) against the tests that call it
(`grep -n "client\.\(get\|post\|put\|delete\)" tests/test_api.py`). Produce a table:
route · happy path tested? · auth/role tested? · validation/error tested?
If `pytest-cov` is available, also run `--cov=backend --cov-report=term-missing`.

## 3. Find gaps

Typical gaps to look for:
- Role checks on every admin route (parametrised `test_admin_only` list complete?).
- Author object-level access: another author's title on `GET/PUT /api/titles/{id}` and `/remove`.
- Validation edges: `DiscountIn` low/high order, blank strings → `None`, `extra` fields rejected,
  password exactly `PASSWORD_MIN` and 72/73 bytes, username pattern.
- Sessions: expired session, `remember` cookie max_age, `MAX_ACCOUNTS` trimming, logout with no body.
- Error mapping: each `DB_MESSAGES` entry produces its status; non-mapped DB error → 503.
- Unit tests that need no database: `models.py` validators, `auth.password_problem`,
  `auth.locked_out` window logic, `main.search_sql`, `main._field`. Put these in a new
  `tests/test_units.py` so they run even when RDS is down.
- Frontend: `lib.js` helpers (`esc`, `date`, `money`, `initials` on empty names) — suggest a small
  `node --test` file if Node is available.

## 4. Write the tests

Follow `tests/test_api.py`: plain functions, `client` fixture, `ADMIN`/`AUTHOR` cookie headers from
`session_for`, assertions on status and on the `errors` field names. Any login or row you create
must be cleaned up in a fixture `finally`. Never set `DL_ALLOW_WRITES=1` except via `monkeypatch`
in a test that removes what it wrote (see `test_add_author_live_then_they_sign_in`).

Run the suite again and report the real result.

## Report

Coverage table, the list of gaps ranked by risk, the tests added (file and names), the final pytest
output line, and recommendations for CI (e.g. run unit tests on every push, DB tests nightly).
