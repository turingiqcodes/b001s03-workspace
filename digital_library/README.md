# Digital Library app

A FastAPI backend on the pubs Postgres database, and a plain HTML/JS frontend (no build step)
served by the same process. The screens follow the mockups in `../svg/`.

## Run it

From the repo root:

```bash
python3 -m venv digital_library/.venv
digital_library/.venv/bin/pip install -r digital_library/requirements-dev.txt
digital_library/.venv/bin/uvicorn digital_library.backend.main:app --port 8000
```

Open http://localhost:8000. The database password comes from `~/.pgpass` (or `PGPASSWORD`);
host, database and user default to the pubs RDS instance and can be changed with the usual
`PGHOST`, `PGDATABASE`, `PGUSER` variables.

## Practice mode (the default)

Saving a form runs the real `INSERT`/`UPDATE`, so every constraint and foreign key is checked,
and then rolls it back: nothing in the 11 pubs tables changes. The form says "All checks passed".
To store changes for real, start the server with `DL_ALLOW_WRITES=1`.

Deleting a book (author) and restoring it (admin) always write, but only to the app's own
`app_title_removals` table; the book's rows in `titles`, `sales` and `roysched` are never deleted.

## Signing in

Everyone signs in with a username and password; there is no sign-up.

1. **Once:** create the `app_sessions` table (and the other app tables) with `sql/app_users.sql`.
2. **The admin's password** is set from the command line, typed at a hidden prompt:
   `digital_library/.venv/bin/python -m digital_library.backend.set_password admin` (run it again to change it).
3. **A new author** (someone not in the authors table yet): **Author Access → Add author**. It saves
   their row in `authors` and, if ticked, their login in `app_users` in one transaction, so either
   both are stored or neither is. Leave Author ID blank to get a free one. In practice mode it only checks.
4. **Author logins** for authors already in the table are created on **Author Access** (Create, then share the
   password privately). The admin can also reset a password or disable a login there; both sign
   the author out at once. Authors can change their own password on My Profile.

**Switching accounts:** click your name in the top right. Several accounts can be signed in on one
browser (up to 5): **Add another account** signs in with that account's password once, then
**Switch to** moves between them with no password. **Sign out** ends only the current account (the
next one becomes active); **Sign out of all accounts** ends every one. On the sign-in page,
accounts still signed in here appear under **Continue as**. `dl_session` holds the active session and
`dl_others` the rest; switching just swaps them.

Passwords are stored as bcrypt hashes. A sign-in sets an HttpOnly, SameSite=Strict `dl_session`
cookie (12 hours, or 30 days with "Remember me"); only the token's SHA-256 is kept in
`app_sessions`. Five wrong passwords for a username in 15 minutes locks it for the rest of that window.
The cookie is `Secure`; behind plain http on a host other than localhost, set `DL_COOKIE_SECURE=0`.

## Library for authors

Authors have a **Library** in the sidebar (mockup `svg/author-library.svg`): every visible book from every
author, with type filters and the top-bar search. It is view only. A book page shows the cover,
authors, publisher, price, date and notes; sales, royalties and advances stay on the admin's pages.
Their own books are tagged **Yours** and link to the edit form in My Books.

## Settings

The sidebar's **Settings** (both roles; mockups `svg/admin-settings.svg`, `svg/author-settings.svg`):

- **Appearance:** Light, Dark or Match system, and Comfortable or Compact density. The sun/moon
  switch in the top bar is the same setting. Kept in this browser (localStorage `dl.prefs`);
  `index.html` applies it before the first paint.
- **Tables** (admin): rows per page for Sales and Employees.
- **Account and security:** username, role, change password, and sign out every other browser.
- **System** (admin, read-only): database, practice or live saving, sign-in length, lockout and
  password rules.


## Layout

| Path | What |
|---|---|
| `backend/main.py` | API routes, role checks, error handling, serves the frontend |
| `backend/auth.py` | Password hashing, sessions, the wrong-password lockout |
| `backend/set_password.py` | Command-line password setter (the admin's first password) |
| `backend/db.py` | Connection pool and the practice-mode write rule |
| `backend/models.py` | Form validation that mirrors the database's column limits and CHECKs |
| `frontend/` | `index.html`, `app.css`, and one JS module per area (`views-admin.js`, `views-author.js`, `views-books.js`, `views-settings.js`) |
| `tests/test_api.py` | API tests against the real database (`cd app && .venv/bin/python -m pytest -q`) |

## API

| Method and path | Who | What |
|---|---|---|
| `GET /api/health` · `POST /api/login` · `POST /api/logout` | anyone | Status · sign in (keeps other signed-in accounts) · sign out (`{"all": true}` for every account) |
| `GET /api/accounts` · `POST /api/accounts/switch` | anyone | Accounts signed in on this browser · make one active |
| `GET /api/me` · `POST /api/me/password` · `GET /api/lookups` | any user | Current user · change own password · select lists |
| `GET /api/settings` · `POST /api/me/sessions/end-others` | any user | Settings page (admin also gets `system`) · sign out other browsers |
| `GET /api/dashboard` | admin | Totals, recent sales, top discounts and publishers |
| `GET /api/titles?q=&type=` · `GET /api/titles/{id}` | admin (author: own book) | Library and book detail (authors, royalty tiers, sales) |
| `POST /api/titles` · `PUT /api/titles/{id}` | admin, author | Add a book (author is linked to themself) · edit a book |
| `POST /api/titles/{id}/remove` · `GET /api/removals` · `DELETE /api/removals/{id}` | author (own) / admin | Hide a book · list hidden books · restore |
| `GET/POST /api/sales` | admin | Order lines with filters and paging · new order line |
| `GET/POST /api/discounts` · `GET /api/stores` | admin | Discount rules · new rule · stores with totals |
| `GET/POST /api/publishers` · `GET/POST /api/employees` | admin | Publishers (+ pub_info) · employees with level check |
| `GET /api/library?q=&type=` · `GET /api/library/{id}` | any user | Read-only catalog of every visible book (no sales or royalties) |
| `GET /api/authors` | admin | Authors with their login status |
| `POST /api/authors` | admin | New author row, optionally with their login (`{author, login}`) |
| `POST /api/logins` · `PUT /api/logins/{username}/password` · `PUT /api/logins/{username}/active` | admin | Create an author login · reset its password · disable or enable it |
| `GET /api/my/books` · `GET /api/my/profile` | author | The author's own books and details |
