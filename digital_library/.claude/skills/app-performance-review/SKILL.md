---
name: app-performance-review
description: Performance and efficiency review of the Digital Library app — SQL query cost, round trips per request, connection pool use, and frontend rendering. Use when asked whether the app is fast/efficient, or to find slow endpoints.
---

# Performance & efficiency — Digital Library app

The pubs data set is small today, so judge each finding by how it scales (rows in `sales`,
`titles`, `app_sessions`) and by round trips to a remote RDS instance (each costs a network hop).

## What to check

**Database round trips**
- Count `db.one`/`db.rows` calls per request. Each takes a pool connection and its own transaction.
  Endpoints such as `lookups`, `dashboard`, `title_detail`, `login`, `browser_accounts` +
  `write_account_cookies` are candidates for merging queries or reusing one `db.cursor()`.
- Loops that query per item (N+1): `browser_accounts` calls `session_user` per token,
  `write_account_cookies` calls `cookie_age` per token, `free_au_id` loops until it finds a free ID.

**Query shape**
- Correlated subqueries in SELECT lists (`AUTHORS_JSON`, `CATALOG_AUTHORS`, counts per publisher).
- `ILIKE '%q%'` search across titles/authors/publishers: no index can help; fine at pubs size, note
  the trigram-index option (`pg_trgm`) if growth is expected.
- `count(DISTINCT ...)`, `max(substr(ord_num,2)::int)` over all sales on every `lookups` call.
- Pagination with OFFSET plus a separate totals query.
- Missing indexes: `app_sessions(token_hash)` (looked up on every request), `app_sessions(user_id)`,
  `app_sessions(expires_at)`, `titleauthor(au_id)`. Check `../sql/app_users.sql` for what exists.
  If the database is reachable, run `EXPLAIN ANALYZE` on the hot queries.

**Concurrency and pool**
- Route functions are sync `def`, so FastAPI runs them in a threadpool (40 threads by default) while
  `ThreadedConnectionPool` max is `DL_DB_POOL` (5). What happens when more than 5 requests run at once?
  (`getconn` raises `PoolError`, it does not wait.)
- bcrypt hashing cost on login and on every `check_login`.
- `DELETE FROM app_sessions WHERE expires_at < now()` on every sign-in.

**Frontend**
- Whole-page `innerHTML` re-renders, repeated `api()` calls for data already loaded (`/api/lookups`),
  listeners attached on every render.

## How to verify

Prefer measurement over guessing: time endpoints with `TestClient` in a scratchpad script, or count
queries by wrapping `db.cursor`. Never enable `DL_ALLOW_WRITES`.

## Report

A table: **Impact** (High/Medium/Low, and at what scale it starts to matter) · **File:line** ·
**Issue** · **Evidence** (query count, timing, plan) · **Fix**. Separate "fix now" from "fix when data grows".
