---
name: app-security-review
description: Security review of the Digital Library app — authentication, sessions, authorization, SQL injection, XSS, secrets and error leakage. Use when asked for a security review/audit of the app folder or of a change to it.
---

# Security review — Digital Library app

The app is a FastAPI service with cookie sessions (`backend/auth.py`), raw psycopg2 SQL
(`backend/main.py`, `backend/db.py`) and a frontend that builds HTML with template strings
(`frontend/*.js`). Review against the checklist below, then report only exploitable findings.

## Checklist

**Authentication (`auth.py`, `/api/login`)**
- bcrypt use: `gensalt()` cost, the 72-byte truncation, the timing-equalising `_DUMMY_HASH`.
- Lockout (`locked_out`/`record_failure`): keyed on `(client IP, username)` — can an attacker rotate
  IPs, or lock a victim out? Is state in-process only (lost on restart, not shared across workers)?
  Is `_fails` bounded in memory?
- Password policy (`PASSWORD_MIN`) versus the data protected.

**Sessions and cookies**
- Token entropy (`secrets.token_urlsafe(32)`), only the SHA-256 stored, expiry enforced in SQL.
- Cookie flags: HttpOnly, Secure (`DL_COOKIE_SECURE`), SameSite=Strict, path.
- Session revocation on password change, reset, disable (`end_all_sessions`).
- `dl_others` account switcher: can a token from one user be used to act as another?

**Authorization**
- For every route in `main.py`: list the dependency used and compare with the README API table.
- Object-level checks: `title_detail`, `edit_title`, `remove_title` for authors (`on_title`).
- Admin-only data (sales, royalties, advances, `password_hash`) must not leak through author or
  library endpoints (`/api/library*`, `/api/my/*`, `SELECT a.*`).

**Injection**
- Every `db.rows`/`db.one`/`write_*` call: values must be `%s` parameters. f-strings may only splice
  constants (`VISIBLE`, `REVENUE`, `AUTHORS_JSON`, `" AND ".join(where)` built from constant strings).
  Flag any f-string that splices request data.
- `ILIKE` patterns built from user input: `%`/`_` wildcards are not injection but note them.

**XSS (frontend)**
- Every `${...}` interpolated into an `innerHTML` template must go through `esc()` unless it is a
  number the code produced, an `icon()` call, or a constant. Attribute values need `esc()` too.
- `href` values built from data (`#/titles/${...}`): `javascript:` is impossible only if the prefix is fixed.

**Errors, config and secrets**
- `database_error` returns `diag.message_primary` and constraint names to the client: what schema
  detail leaks?
- `/api/settings` `system` block, `/api/health` — who can call them and what do they reveal.
- Hard-coded hosts/usernames in `db.py`; no passwords in code or git (`git log -p -- app | grep -i pass`).
- CSRF: SameSite=Strict + JSON bodies; any state-changing GET?
- Dependency versions in `requirements*.txt`: known CVEs (`pip-audit` if available, else note it).

## Report

For each finding: **Severity** (Critical/High/Medium/Low) · **File:line** · **Issue** · **How it is
exploited** (concrete request) · **Fix**. Then a short list of controls that are already done well,
so they are not "fixed" away later. Do not report theoretical issues without an attack path.
