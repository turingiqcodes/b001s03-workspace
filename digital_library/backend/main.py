"""Digital Library API and frontend server.

Run from the repo root:
    digital_library/.venv/bin/uvicorn digital_library.backend.main:app --port 8000
then open http://localhost:8000.

Callers sign in with POST /api/login, which sets the dl_session cookie (see auth.py). Only the
admin creates logins; the admin's own password is set with `python -m digital_library.backend.set_password admin`.
"""
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NoReturn, Optional

import psycopg2.errors
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db
from .models import (
    PAYTERM_VALUES,
    TYPE_LABEL,
    BookFields,
    BookIn,
    ChangePasswordIn,
    DiscountIn,
    EmployeeIn,
    LoginActiveIn,
    LoginIn,
    LogoutIn,
    NewAuthorIn,
    NewLoginIn,
    PasswordIn,
    PublisherIn,
    SaleIn,
    SwitchIn,
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app):
    """Nothing to do at startup (the pool opens lazily); close all DB connections on shutdown."""
    yield
    db.close()


app = FastAPI(title="Digital Library API", lifespan=lifespan)


# ---------------------------------------------------------------- errors
FIX_FIELDS = "Please fix the highlighted fields."


def _field(loc):
    names = [str(p) for p in loc if p not in ("body", "query", "path")]
    return names[-1] if names else "form"


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    errors = {}
    for e in exc.errors():
        msg = e["msg"].removeprefix("Value error, ")
        if e["type"] == "string_pattern_mismatch":
            msg = "This doesn't have the right format."
        errors.setdefault(_field(e["loc"]), msg)
    return JSONResponse(status_code=422, content={"detail": FIX_FIELDS, "errors": errors})


DB_MESSAGES = {
    psycopg2.errors.UniqueViolation: (409, "Something with this ID already exists."),
    psycopg2.errors.ForeignKeyViolation: (400, "This refers to something that doesn't exist."),
    psycopg2.errors.CheckViolation: (400, "The database rejected one of the values."),
    psycopg2.errors.NotNullViolation: (400, "A required value is missing."),
    psycopg2.errors.DataError: (400, "One of the values is too large or not valid for the database."),
}


@app.exception_handler(psycopg2.Error)
async def database_error(_request: Request, exc: psycopg2.Error):
    """Postgres's own message stays in the server log: it can quote the rejected values. The constraint name is
    safe to return and tells callers (and tests) which rule failed."""
    for kind, (status, message) in DB_MESSAGES.items():
        if isinstance(exc, kind):
            log.info("database rejected a write: %s", exc.diag.message_primary)
            return JSONResponse(status_code=status, content={"detail": message, "constraint": exc.diag.constraint_name})
    if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        log.warning("database unavailable: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "The database is not reachable right now."})
    log.exception("unexpected database error")
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server."})


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=body)


def fail(status: int, detail: str, **errors: str) -> NoReturn:
    """Every error body is {"detail": message, "errors": {field: message}?}."""
    raise HTTPException(status_code=status, detail={"detail": detail, "errors": errors} if errors else detail)


def field_error(status: int = 422, **errors: str) -> NoReturn:
    """A form error: the generic message, plus one message under each highlighted field."""
    fail(status, FIX_FIELDS, **errors)


# ---------------------------------------------------------------- who is calling
def current_user(dl_session: Optional[str] = Cookie(None)):
    user = auth.session_user(dl_session)
    if not user:
        fail(401, "Please sign in.")
    return user


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        fail(403, "Only the admin can see this.")
    return user


def author(user=Depends(current_user)):
    if user["role"] != "author":
        fail(403, "Only an author can see this.")
    return user


def saved(committed, **extra):
    return {"saved": committed, "dry_run": not committed, **extra}


# ---------------------------------------------------------------- shared
def search_sql(q):
    """Library search: title, title ID, any author's name, publisher, or type (code or label). Returns (sql, params)."""
    like = f"%{q.strip()}%"
    types = [code for code, label in TYPE_LABEL.items() if q.strip().lower() in (label + " " + code).lower()]
    return ("""(t.title ILIKE %s OR t.title_id ILIKE %s OR trim(t.type) = ANY(%s)
                OR EXISTS (SELECT 1 FROM public.publishers sp WHERE sp.pub_id = t.pub_id AND sp.pub_name ILIKE %s)
                OR EXISTS (SELECT 1 FROM public.titleauthor ta JOIN public.authors a USING (au_id)
                           WHERE ta.title_id = t.title_id AND a.au_fname || ' ' || a.au_lname ILIKE %s))""",
            [like, like, types, like, like])


VISIBLE = "NOT EXISTS (SELECT 1 FROM public.app_title_removals r WHERE r.title_id = t.title_id)"
REVENUE = "sum(s.qty * t.price)"


@app.get("/api/health")
def health():
    db.one("SELECT 1 AS ok")
    return {"db": "ok", "writes": db.ALLOW_WRITES}


# ---------------------------------------------------------------- signing in
# Several accounts can be signed in on one browser (like an email account switcher). dl_session is the
# active one; dl_others holds the rest. Switching swaps which session is active: no password is re-typed,
# but every account in the list signed in with its own password first.
def browser_accounts(dl_session, dl_others):
    """[(token, user)] for the valid sessions in this browser's cookies, active first, one per user."""
    found, seen = [], set()
    for token in [dl_session, *(dl_others or "").split(".")]:
        if not token or token in seen:
            continue
        seen.add(token)
        user = auth.session_user(token)
        if user and user["user_id"] not in {u["user_id"] for _, u in found}:
            found.append((token, user))
    return found[:auth.MAX_ACCOUNTS]


def write_account_cookies(response, accounts):
    def put(name, value, max_age):
        response.set_cookie(name, value, max_age=max_age, httponly=True, secure=auth.COOKIE_SECURE, samesite="strict", path="/")

    def drop(name):
        response.delete_cookie(name, path="/", httponly=True, secure=auth.COOKIE_SECURE, samesite="strict")

    if not accounts:
        drop(auth.COOKIE)
        drop(auth.OTHERS_COOKIE)
        return
    put(auth.COOKIE, accounts[0][0], auth.cookie_age(accounts[0][0]))
    others = [t for t, _ in accounts[1:]]
    if others:
        ages = [auth.cookie_age(t) for t in others]
        put(auth.OTHERS_COOKIE, ".".join(others), max(ages) if all(a is not None for a in ages) else None)
    else:
        drop(auth.OTHERS_COOKIE)


def account_list(accounts):
    return [{"username": u["username"], "name": u["name"], "role": u["role"], "active": i == 0} for i, (_, u) in enumerate(accounts)]


@app.post("/api/login")
def login(creds: LoginIn, request: Request, response: Response,
          dl_session: Optional[str] = Cookie(None), dl_others: Optional[str] = Cookie(None)):
    """Sign in. Accounts already signed in on this browser stay signed in, ready to switch back to."""
    username = creds.username.strip().lower()
    key = (request.client.host if request.client else "", username)
    if auth.locked_out(key):
        fail(429, f"Too many wrong passwords. Try again in {auth.FAIL_WINDOW // 60} minutes, or ask your admin.")
    user = auth.check_login(username, creds.password)
    if not user:
        auth.record_failure(key)
        fail(401, "That username and password don't match an active login.")
    auth.clear_failures(key)
    token, _ = auth.start_session(user["user_id"], creds.remember)
    kept = []
    for old, u in browser_accounts(dl_session, dl_others):
        if u["user_id"] == user["user_id"] or len(kept) >= auth.MAX_ACCOUNTS - 1:
            auth.end_session(old)        # the same account signing in again, or one too many
        else:
            kept.append((old, u))
    write_account_cookies(response, [(token, auth.session_user(token)), *kept])
    return me(auth.session_user(token))


@app.get("/api/accounts")
def accounts(response: Response, dl_session: Optional[str] = Cookie(None), dl_others: Optional[str] = Cookie(None)):
    """Accounts signed in on this browser (works signed out too, for "Continue as" on the sign-in page)."""
    found = browser_accounts(dl_session, dl_others)
    write_account_cookies(response, found)   # drops expired or signed-out sessions from the cookies
    return account_list(found)


@app.post("/api/accounts/switch")
def switch_account(body: SwitchIn, response: Response,
                   dl_session: Optional[str] = Cookie(None), dl_others: Optional[str] = Cookie(None)):
    found = browser_accounts(dl_session, dl_others)
    target = next((a for a in found if a[1]["username"] == body.username.strip().lower()), None)
    if not target:
        fail(404, "That account is not signed in on this browser. Sign in to add it.")
    write_account_cookies(response, [target, *[a for a in found if a is not target]])
    return me(target[1])


@app.post("/api/logout")
def logout(response: Response, body: Optional[LogoutIn] = None,
           dl_session: Optional[str] = Cookie(None), dl_others: Optional[str] = Cookie(None)):
    """Sign out the active account (the next one signed in here becomes active), or every account with {"all": true}."""
    found = browser_accounts(dl_session, dl_others)
    leaving = found if body and body.all else found[:1]
    for token, _ in leaving:
        auth.end_session(token)
    auth.end_session(dl_session)
    rest = [a for a in found if a not in leaving]
    write_account_cookies(response, rest)
    return {"signed_out": True, "me": me(rest[0][1]) if rest else None}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {**user, "writes": db.ALLOW_WRITES}


@app.post("/api/me/password")
def change_my_password(body: ChangePasswordIn, user=Depends(current_user), dl_session: Optional[str] = Cookie(None)):
    if not auth.check_login(user["username"], body.current_password):
        field_error(current_password="That is not your current password.")
    set_password(user["user_id"], body.password, keep_token=dl_session)
    return {"changed": True}


@app.get("/api/settings")
def settings(user=Depends(current_user)):
    """The Settings page: the signed-in account, and for the admin how this server is set up (read-only)."""
    out = {"account": db.one("""SELECT u.username, u.role, u.created_at, u.last_login,
                                       (SELECT count(*) FROM public.app_sessions s WHERE s.user_id = u.user_id AND s.expires_at > now()) AS sessions
                                FROM public.app_users u WHERE u.user_id = %s""", (user["user_id"],))}
    if user["role"] == "admin":
        out["system"] = {
            "database": f"{db.CONNECT['dbname']} on {db.CONNECT['host'].split('.')[0]}",
            "writes": db.ALLOW_WRITES,
            "session_hours": auth.SESSION_HOURS, "remember_days": auth.REMEMBER_DAYS,
            "fail_limit": auth.FAIL_LIMIT, "fail_window_minutes": auth.FAIL_WINDOW // 60,
            "password_min": auth.PASSWORD_MIN,
            **db.one("""SELECT count(*) FILTER (WHERE is_active AND role = 'author') AS active_authors,
                               (SELECT count(DISTINCT user_id) FROM public.app_sessions WHERE expires_at > now()) AS signed_in_users
                        FROM public.app_users"""),
        }
    return out


@app.post("/api/me/sessions/end-others")
def end_other_sessions(user=Depends(current_user), dl_session: Optional[str] = Cookie(None)):
    auth.end_all_sessions(user["user_id"], keep_token=dl_session)
    return {"signed_out_others": True}


def check_password(password: str) -> None:
    problem = auth.password_problem(password)
    if problem:
        field_error(password=problem)


def set_password(user_id, password, keep_token=None):
    check_password(password)
    db.write_app("UPDATE public.app_users SET password_hash = %s WHERE user_id = %s", (auth.hash_password(password), user_id))
    auth.end_all_sessions(user_id, keep_token)


@app.get("/api/lookups")
def lookups(_user=Depends(current_user)):
    return {
        "stores": db.rows("SELECT stor_id, stor_name, city, state FROM public.stores ORDER BY stor_name, stor_id"),
        "publishers": db.rows("SELECT pub_id, pub_name FROM public.publishers ORDER BY pub_name"),
        "titles": db.rows(f"SELECT t.title_id, t.title, t.price FROM public.titles t WHERE {VISIBLE} ORDER BY t.title"),
        "authors": db.rows("SELECT au_id, au_fname || ' ' || au_lname AS name FROM public.authors ORDER BY au_lname, au_fname"),
        "jobs": db.rows("SELECT job_id, job_desc, min_lvl, max_lvl FROM public.jobs ORDER BY job_desc"),
        "discounts": db.rows("SELECT discounttype, stor_id, lowqty, highqty, discount FROM public.discounts"),
        "types": [{"value": k, "label": v} for k, v in TYPE_LABEL.items()],
        "payterms": PAYTERM_VALUES,
        "next_order": db.one("""SELECT 'N' || lpad((coalesce(max(substr(ord_num, 2)::int), 0) + 1)::text, 4, '0') AS n
                                FROM public.sales WHERE ord_num ~ '^N[0-9]{4}$'""")["n"],
    }


# ---------------------------------------------------------------- admin: dashboard
@app.get("/api/dashboard")
def dashboard(_user=Depends(admin)):
    totals = db.one(f"""SELECT count(DISTINCT (s.stor_id, s.ord_num)) AS orders, count(*) AS order_lines,
                               coalesce({REVENUE}, 0) AS revenue, coalesce(sum(s.qty), 0) AS units,
                               (SELECT count(*) FROM public.stores) AS stores,
                               (SELECT count(*) FROM public.publishers) AS publishers,
                               (SELECT count(*) FROM public.titles) AS titles,
                               (SELECT count(*) FROM public.employee) AS employees,
                               (SELECT count(DISTINCT pub_id) FROM public.employee) AS employers
                        FROM public.sales s JOIN public.titles t USING (title_id)""")
    return {
        "totals": totals,
        "recent_sales": recent_sales(9, 0)["rows"],
        "discounts": db.rows("""SELECT d.discounttype, coalesce(st.stor_name, 'All stores') AS applies_to, d.lowqty, d.highqty, d.discount
                                FROM public.discounts d LEFT JOIN public.stores st USING (stor_id)
                                ORDER BY d.discount DESC LIMIT 3"""),
        "discount_count": db.one("SELECT count(*) AS n FROM public.discounts")["n"],
        "publishers": db.rows("""SELECT p.pub_id, p.pub_name,
                                        (SELECT count(*) FROM public.titles t WHERE t.pub_id = p.pub_id) AS titles,
                                        (SELECT count(*) FROM public.employee e WHERE e.pub_id = p.pub_id) AS staff
                                 FROM public.publishers p ORDER BY titles DESC, p.pub_name LIMIT 5"""),
    }


# ---------------------------------------------------------------- titles
def title_row(title_id):
    t = db.one(f"""SELECT t.*, p.pub_name, NOT ({VISIBLE}) AS removed
                   FROM public.titles t LEFT JOIN public.publishers p USING (pub_id) WHERE t.title_id = %s""", (title_id,))
    if not t:
        fail(404, "No book with that ID.")
    return t


def on_title(au_id, title_id):
    return db.exists("SELECT 1 FROM public.titleauthor WHERE au_id = %s AND title_id = %s", (au_id, title_id))


def require_own_title(user, title_id: str, verb: str) -> None:
    """An author may only touch books they are on; the admin may touch any."""
    if user["role"] == "author" and not on_title(user["au_id"], title_id):
        fail(403, f"You can only {verb} your own books.")


AUTHORS_JSON = """coalesce((SELECT json_agg(json_build_object('au_id', a.au_id, 'name', a.au_fname || ' ' || a.au_lname,
                                                     'au_ord', ta.au_ord, 'royaltyper', ta.royaltyper) ORDER BY ta.au_ord)
                            FROM public.titleauthor ta JOIN public.authors a USING (au_id)
                            WHERE ta.title_id = t.title_id), '[]')"""


@app.get("/api/titles")
def titles(q: Optional[str] = None, type: Optional[str] = None, _user=Depends(admin)):
    where, params = [VISIBLE], []
    if type:
        where.append("t.type = %s")
        params.append(type)
    if q and q.strip():
        sql, more = search_sql(q)
        where.append(sql)
        params += more
    found = db.rows(f"""SELECT t.title_id, t.title, t.type, t.price, t.ytd_sales, p.pub_name, {AUTHORS_JSON} AS authors
                        FROM public.titles t LEFT JOIN public.publishers p USING (pub_id)
                        WHERE {' AND '.join(where)}
                        ORDER BY t.ytd_sales DESC NULLS LAST, t.title""", params)
    by_type = db.rows(f"SELECT t.type, count(*) AS n FROM public.titles t WHERE {VISIBLE} GROUP BY t.type ORDER BY n DESC, t.type")
    return {"rows": found, "by_type": by_type, "total": sum(r["n"] for r in by_type)}


@app.get("/api/titles/{title_id}")
def title_detail(title_id: str, user=Depends(current_user)):
    t = title_row(title_id)
    require_own_title(user, title_id, "see")
    authors = db.rows("""SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS name, ta.au_ord, ta.royaltyper
                         FROM public.titleauthor ta JOIN public.authors a USING (au_id)
                         WHERE ta.title_id = %s ORDER BY ta.au_ord""", (title_id,))
    sales = db.rows("""SELECT s.stor_id, s.ord_num, s.ord_date, st.stor_name, s.qty, s.payterms, s.qty * t.price AS amount
                       FROM public.sales s JOIN public.stores st USING (stor_id) JOIN public.titles t USING (title_id)
                       WHERE s.title_id = %s ORDER BY s.ord_date DESC, s.ord_num""", (title_id,))
    return {
        "title": t,
        "authors": authors,
        "share_total": sum(a["royaltyper"] or 0 for a in authors),
        "tiers": db.rows("SELECT lorange, hirange, royalty FROM public.roysched WHERE title_id = %s ORDER BY lorange", (title_id,)),
        "sales": sales,
        "summary": {"units": sum(s["qty"] for s in sales),
                    "revenue": None if t["price"] is None else sum(s["amount"] for s in sales),
                    "orders": len({(s["stor_id"], s["ord_num"]) for s in sales}),
                    "stores": len({s["stor_id"] for s in sales})},
    }


@app.post("/api/titles")
def add_title(book: BookIn, user=Depends(current_user)):
    if user["role"] == "author":
        au_id = user["au_id"]
    elif not book.au_id:
        field_error(au_id="Pick the book's author.")
    else:
        au_id = book.au_id
    committed = db.write_pubs([
        ("""INSERT INTO public.titles (title_id, title, type, pub_id, price, advance, royalty, notes, pubdate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, CURRENT_DATE))""",
         (book.title_id, book.title, book.type, book.pub_id, book.price, book.advance, book.royalty, book.notes, book.pubdate)),
        ("INSERT INTO public.titleauthor (au_id, title_id, au_ord, royaltyper) VALUES (%s, %s, 1, 100)", (au_id, book.title_id)),
    ])
    return saved(committed, title_id=book.title_id, au_id=au_id)


@app.put("/api/titles/{title_id}")
def edit_title(title_id: str, book: BookFields, user=Depends(current_user)):
    title_row(title_id)
    require_own_title(user, title_id, "edit")
    committed = db.write_pubs([(
        """UPDATE public.titles SET title = %s, type = %s, pub_id = %s, price = %s, advance = %s, royalty = %s,
                                    notes = %s, pubdate = coalesce(%s, pubdate)
           WHERE title_id = %s""",
        (book.title, book.type, book.pub_id, book.price, book.advance, book.royalty, book.notes, book.pubdate, title_id))])
    return saved(committed, title_id=title_id)


@app.post("/api/titles/{title_id}/remove")
def remove_title(title_id: str, user=Depends(current_user)):
    """Hide a book (app_title_removals). Its rows in the pubs tables stay; the admin can restore it."""
    title_row(title_id)
    require_own_title(user, title_id, "delete")
    db.write_app("""INSERT INTO public.app_title_removals (title_id, removed_by) VALUES (%s, %s)
                    ON CONFLICT (title_id) DO NOTHING""", (title_id, user["user_id"]))
    return {"removed": True, "title_id": title_id}


@app.get("/api/removals")
def removals(_user=Depends(admin)):
    return db.rows("""SELECT r.title_id, t.title, u.username AS removed_by, r.removed_at
                      FROM public.app_title_removals r JOIN public.titles t USING (title_id)
                      JOIN public.app_users u ON u.user_id = r.removed_by ORDER BY r.removed_at DESC""")


@app.delete("/api/removals/{title_id}")
def restore(title_id: str, _user=Depends(admin)):
    if not db.write_app("DELETE FROM public.app_title_removals WHERE title_id = %s", (title_id,)):
        fail(404, "That book is not removed.")
    return {"restored": True, "title_id": title_id}


# ---------------------------------------------------------------- library for every user (read-only catalog)
# Authors browse every visible book here. Only catalog facts: sales, royalties and advances stay with the admin.
CATALOG_AUTHORS = """coalesce((SELECT json_agg(a.au_fname || ' ' || a.au_lname ORDER BY ta.au_ord)
                              FROM public.titleauthor ta JOIN public.authors a USING (au_id)
                              WHERE ta.title_id = t.title_id), '[]')"""


def mine_sql(user):
    """SQL for "the signed-in author is on this title" (always false for the admin), and its params."""
    return "EXISTS (SELECT 1 FROM public.titleauthor m WHERE m.title_id = t.title_id AND m.au_id = %s)", [user["au_id"]]


@app.get("/api/library")
def catalog(q: Optional[str] = None, type: Optional[str] = None, user=Depends(current_user)):
    mine, params = mine_sql(user)
    where = [VISIBLE]
    if type:
        where.append("t.type = %s")
        params.append(type)
    if q and q.strip():
        sql, more = search_sql(q)
        where.append(sql)
        params += more
    found = db.rows(f"""SELECT t.title_id, t.title, t.type, t.price, t.pubdate, p.pub_name, {CATALOG_AUTHORS} AS authors, {mine} AS mine
                        FROM public.titles t LEFT JOIN public.publishers p USING (pub_id)
                        WHERE {' AND '.join(where)}
                        ORDER BY t.title""", params)
    by_type = db.rows(f"SELECT t.type, count(*) AS n FROM public.titles t WHERE {VISIBLE} GROUP BY t.type ORDER BY n DESC, t.type")
    return {"rows": found, "by_type": by_type, "total": sum(r["n"] for r in by_type)}


@app.get("/api/library/{title_id}")
def catalog_book(title_id: str, user=Depends(current_user)):
    mine, params = mine_sql(user)
    t = db.one(f"""SELECT t.title_id, t.title, t.type, t.price, t.pubdate, t.notes, p.pub_name, {CATALOG_AUTHORS} AS authors, {mine} AS mine
                   FROM public.titles t LEFT JOIN public.publishers p USING (pub_id)
                   WHERE t.title_id = %s AND {VISIBLE}""", params + [title_id])
    if not t:
        fail(404, "No book with that ID in the library.")
    return t


# ---------------------------------------------------------------- sales
def recent_sales(limit, offset, stor_id=None, payterms=None):
    where, params = ["TRUE"], []
    if stor_id:
        where.append("s.stor_id = %s")
        params.append(stor_id)
    if payterms:
        where.append("s.payterms = %s")
        params.append(payterms)
    cond = " AND ".join(where)
    found = db.rows(f"""SELECT s.stor_id, s.ord_num, s.ord_date, st.stor_name, s.title_id, t.title, s.qty, t.price,
                               s.qty * t.price AS amount, s.payterms
                        FROM public.sales s JOIN public.stores st USING (stor_id) JOIN public.titles t USING (title_id)
                        WHERE {cond} ORDER BY s.ord_date DESC, s.ord_num DESC, s.title_id
                        LIMIT %s OFFSET %s""", params + [limit, offset])
    totals = db.one(f"""SELECT count(*) AS lines, count(DISTINCT (s.stor_id, s.ord_num)) AS orders, coalesce(sum(s.qty), 0) AS units,
                               coalesce({REVENUE}, 0) AS revenue, count(DISTINCT s.stor_id) AS stores_selling,
                               (SELECT count(*) FROM public.stores) AS stores
                        FROM public.sales s JOIN public.titles t USING (title_id) WHERE {cond}""", params)
    return {"rows": found, "totals": totals}


@app.get("/api/sales")
def sales(stor_id: Optional[str] = None, payterms: Optional[str] = None,
          page: int = Query(1, ge=1), page_size: int = Query(8, ge=1, le=100), _user=Depends(admin)):
    result = recent_sales(page_size, (page - 1) * page_size, stor_id, payterms)
    return {**result, "page": page, "page_size": page_size}


@app.post("/api/sales")
def add_sale(sale: SaleIn, _user=Depends(admin)):
    committed = db.write_pubs([("""INSERT INTO public.sales (stor_id, ord_num, ord_date, qty, payterms, title_id)
                                   VALUES (%s, %s, %s, %s, %s, %s)""",
                                (sale.stor_id, sale.ord_num, sale.ord_date, sale.qty, sale.payterms, sale.title_id))])
    return saved(committed, ord_num=sale.ord_num)


# ---------------------------------------------------------------- discounts and stores
@app.get("/api/discounts")
def discounts(_user=Depends(admin)):
    return db.rows("""SELECT d.discounttype, d.stor_id, coalesce(st.stor_name, 'All stores') AS applies_to,
                             st.city, st.state, d.lowqty, d.highqty, d.discount
                      FROM public.discounts d LEFT JOIN public.stores st USING (stor_id)
                      ORDER BY d.discounttype, d.discount DESC""")


@app.post("/api/discounts")
def add_discount(d: DiscountIn, _user=Depends(admin)):
    committed = db.write_pubs([("""INSERT INTO public.discounts (discounttype, stor_id, lowqty, highqty, discount)
                                   VALUES (%s, %s, %s, %s, %s)""", (d.discounttype, d.stor_id, d.lowqty, d.highqty, d.discount))])
    return saved(committed)


@app.get("/api/stores")
def stores(_user=Depends(admin)):
    return db.rows(f"""SELECT st.stor_id, st.stor_name, st.stor_address, st.city, st.state, st.zip,
                              count(DISTINCT s.ord_num) AS orders, coalesce(sum(s.qty), 0) AS units,
                              coalesce({REVENUE}, 0) AS revenue,
                              (SELECT max(d.discount) FROM public.discounts d WHERE d.stor_id = st.stor_id) AS own_discount
                       FROM public.stores st
                       LEFT JOIN public.sales s USING (stor_id) LEFT JOIN public.titles t USING (title_id)
                       GROUP BY st.stor_id ORDER BY st.stor_id""")


# ---------------------------------------------------------------- publishers and employees
@app.get("/api/publishers")
def publishers(_user=Depends(admin)):
    return db.rows("""SELECT p.pub_id, p.pub_name, p.city, p.state, p.country, pi.pr_info,
                             (SELECT count(*) FROM public.titles t WHERE t.pub_id = p.pub_id) AS titles,
                             (SELECT count(*) FROM public.employee e WHERE e.pub_id = p.pub_id) AS employees,
                             (SELECT avg(t.price) FROM public.titles t WHERE t.pub_id = p.pub_id) AS avg_price
                      FROM public.publishers p LEFT JOIN public.pub_info pi USING (pub_id) ORDER BY p.pub_id""")


@app.post("/api/publishers")
def add_publisher(p: PublisherIn, _user=Depends(admin)):
    statements = [("INSERT INTO public.publishers (pub_id, pub_name, city, state, country) VALUES (%s, %s, %s, %s, %s)",
                   (p.pub_id, p.pub_name, p.city, p.state.upper() if p.state else None, p.country))]
    if p.pr_info:
        statements.append(("INSERT INTO public.pub_info (pub_id, pr_info) VALUES (%s, %s)", (p.pub_id, p.pr_info)))
    return saved(db.write_pubs(statements), pub_id=p.pub_id)


@app.get("/api/employees")
def employees(pub_id: Optional[str] = None, job_id: Optional[int] = None, q: Optional[str] = None,
              page: int = Query(1, ge=1), page_size: int = Query(8, ge=1, le=100), _user=Depends(admin)):
    where, params = ["TRUE"], []
    if pub_id:
        where.append("e.pub_id = %s")
        params.append(pub_id)
    if job_id:
        where.append("e.job_id = %s")
        params.append(job_id)
    if q:
        where.append("(e.fname || ' ' || e.lname ILIKE %s OR e.emp_id ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    cond = " AND ".join(where)
    found = db.rows(f"""SELECT e.emp_id, e.fname, e.minit, e.lname, e.pub_id, p.pub_name, e.job_id, j.job_desc,
                               e.job_lvl, j.min_lvl, j.max_lvl, e.job_lvl BETWEEN j.min_lvl AND j.max_lvl AS level_in_range, e.hire_date
                        FROM public.employee e JOIN public.jobs j USING (job_id) JOIN public.publishers p USING (pub_id)
                        WHERE {cond} ORDER BY e.lname, e.fname LIMIT %s OFFSET %s""", params + [page_size, (page - 1) * page_size])
    totals = db.one(f"""SELECT count(*) AS employees, count(DISTINCT e.job_id) AS jobs_in_use, count(DISTINCT e.pub_id) AS publishers,
                               max(e.hire_date) AS newest_hire,
                               count(*) FILTER (WHERE NOT e.job_lvl BETWEEN j.min_lvl AND j.max_lvl) AS out_of_range,
                               (SELECT count(*) FROM public.jobs) AS jobs
                        FROM public.employee e JOIN public.jobs j USING (job_id) WHERE {cond}""", params)
    return {"rows": found, "totals": totals, "page": page, "page_size": page_size}


@app.post("/api/employees")
def add_employee(e: EmployeeIn, _user=Depends(admin)):
    job = db.one("SELECT job_desc, min_lvl, max_lvl FROM public.jobs WHERE job_id = %s", (e.job_id,))
    if not job:
        field_error(job_id="Pick a job from the list.")
    if not job["min_lvl"] <= e.job_lvl <= job["max_lvl"]:
        field_error(job_lvl=f"{job['job_desc']} allows levels {job['min_lvl']} – {job['max_lvl']}.")
    committed = db.write_pubs([("""INSERT INTO public.employee (emp_id, fname, minit, lname, job_id, job_lvl, pub_id, hire_date)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s, CURRENT_DATE))""",
                                (e.emp_id.upper(), e.fname, e.minit, e.lname, e.job_id, e.job_lvl, e.pub_id, e.hire_date))])
    return saved(committed, emp_id=e.emp_id.upper())


# ---------------------------------------------------------------- admin: author access
@app.get("/api/authors")
def authors(_user=Depends(admin)):
    return db.rows("""SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS name, a.city, a.state, a.contract,
                             (SELECT count(*) FROM public.titleauthor ta WHERE ta.au_id = a.au_id) AS books,
                             u.username, u.is_active, u.created_at, u.last_login, u.password_hash IS NOT NULL AS has_password
                      FROM public.authors a LEFT JOIN public.app_users u ON u.au_id = a.au_id
                      ORDER BY u.user_id IS NULL, a.au_lname, a.au_fname""")


def free_au_id():
    """An unused author ID in the pubs format (999-99-9999)."""
    while True:
        au_id = f"{secrets.randbelow(900) + 100}-{secrets.randbelow(90) + 10}-{secrets.randbelow(9000) + 1000}"
        if not db.exists("SELECT 1 FROM public.authors WHERE au_id = %s", (au_id,)):
            return au_id


def check_username_free(username: str) -> None:
    if db.exists("SELECT 1 FROM public.app_users WHERE username = %s", (username,)):
        field_error(409, username="That username is taken.")


@app.post("/api/authors")
def add_author(body: NewAuthorIn, user=Depends(admin)):
    """A new person in the authors table and, if asked, their login, in one transaction (both or neither)."""
    a, login = body.author, body.login
    if a.au_id and db.exists("SELECT 1 FROM public.authors WHERE au_id = %s", (a.au_id,)):
        field_error(409, au_id="An author with this ID already exists.")
    if login:
        check_username_free(login.username)
        check_password(login.password)
    au_id = a.au_id or free_au_id()
    statements = [("""INSERT INTO public.authors (au_id, au_fname, au_lname, phone, address, city, state, zip, contract)
                      VALUES (%s, %s, %s, coalesce(%s, 'UNKNOWN'), %s, %s, %s, %s, %s)""",
                   (au_id, a.au_fname, a.au_lname, a.phone, a.address, a.city, a.state.upper() if a.state else None, a.zip, a.contract))]
    if login:
        statements.append(("""INSERT INTO public.app_users (username, password_hash, role, au_id, created_by)
                              VALUES (%s, %s, 'author', %s, %s)""",
                           (login.username, auth.hash_password(login.password), au_id, user["user_id"])))
    committed = db.write_pubs(statements)
    return saved(committed, au_id=au_id, name=f"{a.au_fname} {a.au_lname}", username=login.username if login else None)


def author_login(username):
    found = db.one("SELECT user_id FROM public.app_users WHERE username = %s AND role = 'author'", (username,))
    if not found:
        fail(404, "No author login with that username.")
    return found["user_id"]


@app.post("/api/logins")
def create_login(body: NewLoginIn, user=Depends(admin)):
    """An author login, with its first password. Always written (app_users is the app's own table)."""
    if not db.exists("SELECT 1 FROM public.authors WHERE au_id = %s", (body.au_id,)):
        field_error(au_id="No author with that ID.")
    if db.exists("SELECT 1 FROM public.app_users WHERE au_id = %s", (body.au_id,)):
        fail(409, "This author already has a login.")
    check_username_free(body.username)
    check_password(body.password)
    db.write_app("""INSERT INTO public.app_users (username, password_hash, role, au_id, created_by)
                    VALUES (%s, %s, 'author', %s, %s)""",
                 (body.username, auth.hash_password(body.password), body.au_id, user["user_id"]))
    return {"created": True, "username": body.username}


@app.put("/api/logins/{username}/password")
def reset_password(username: str, body: PasswordIn, _user=Depends(admin)):
    """Set a new password for an author; they are signed out everywhere."""
    set_password(author_login(username), body.password)
    return {"changed": True, "username": username}


@app.put("/api/logins/{username}/active")
def set_active(username: str, body: LoginActiveIn, _user=Depends(admin)):
    user_id = author_login(username)
    db.write_app("UPDATE public.app_users SET is_active = %s WHERE user_id = %s", (body.is_active, user_id))
    if not body.is_active:
        auth.end_all_sessions(user_id)
    return {"username": username, "is_active": body.is_active}


# ---------------------------------------------------------------- author: my books and profile
@app.get("/api/my/books")
def my_books(user=Depends(author)):
    return db.rows(f"""SELECT t.title_id, t.title, t.type, t.pub_id, p.pub_name, t.price, t.advance, t.royalty, t.pubdate,
                              t.ytd_sales, t.notes, ta.au_ord, ta.royaltyper,
                              (SELECT count(*) FROM public.titleauthor x WHERE x.title_id = t.title_id) AS author_count,
                              (SELECT coalesce(sum(s.qty), 0) FROM public.sales s WHERE s.title_id = t.title_id) AS units_sold
                       FROM public.titleauthor ta JOIN public.titles t USING (title_id)
                       LEFT JOIN public.publishers p USING (pub_id)
                       WHERE ta.au_id = %s AND {VISIBLE}
                       ORDER BY t.pubdate DESC NULLS LAST, t.title""", (user["au_id"],))


@app.get("/api/my/profile")
def my_profile(user=Depends(author)):
    return db.one("""SELECT a.*, u.username, u.created_at, u.last_login
                     FROM public.authors a JOIN public.app_users u USING (au_id) WHERE a.au_id = %s""", (user["au_id"],))


# ---------------------------------------------------------------- frontend
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
