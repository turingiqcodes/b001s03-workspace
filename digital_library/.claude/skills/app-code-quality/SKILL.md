---
name: app-code-quality
description: Code quality review of the Digital Library app — duplication, structure, naming, consistency between backend and frontend, and maintainability. Use when asked about code quality, refactoring opportunities, or cleanups in the app folder.
---

# Code quality — Digital Library app

Judge the code against its own conventions first: short docstrings that explain *why*, SQL kept
inline next to the route, `fail(status, detail, **field_errors)` for every error, one JS module per
screen area, and `esc()` for every interpolation. Recommend changes that make the next edit safer,
not rewrites for taste.

## What to check

- **Duplication that can drift:**
  - `TYPE_LABEL` in `backend/main.py` and `frontend/lib.js`; payterms in `models.PAYTERMS` and
    `lookups()`; the USERNAME lowercasing validator in both `NewLoginIn` and `AuthorLoginIn`.
  - Repeated SQL fragments (author name concatenation, `by_type` query in `titles` and `catalog`).
  - Existence checks (`SELECT 1 AS yes ...`) repeated across `add_author` and `create_login`.
  - `"Please fix the highlighted fields."` repeated in many `fail()` calls.
- **Size and structure:** `main.py` is one 700-line module. Would routers per area
  (`auth`, `titles`, `sales`, `admin`) help, and what would they cost?
- **Dead or unused code:** imports, models, helpers, CSS classes, JS exports nobody uses.
  (`grep -rn` each export before claiming it is unused.)
- **Error handling consistency:** every route returns `{"detail", "errors"?}`; DB errors map through
  `DB_MESSAGES`; no bare `except`.
- **Naming and types:** route functions that shadow other names (`sales`, `titles`, `authors`,
  `settings`, `me` are both routes and local names); missing type hints on helpers.
- **Config:** hard-coded defaults in `db.py`; magic numbers (`9` recent sales, `LIMIT 3`, `LIMIT 5`).
- **Tooling:** is there a linter/formatter config (ruff, black, eslint)? If ruff is installed run
  `ruff check backend tests`; otherwise suggest it rather than claim lint results.
- **Docs:** README API table matches the routes (`grep -n "@app\." backend/main.py`).

## Report

Group by theme (Duplication, Structure, Consistency, Tooling, Docs). Each item: **File:line** ·
**What** · **Why it matters** · **Suggested change** · **Effort** (S/M/L). End with the top 3 changes
worth doing first.
