"""API tests against the real pubs database (read-only for the pubs tables).

    cd app && .venv/bin/python -m pytest -q

Writes run in practice mode (rolled back), and the test that removes a book restores it again.
The admin and clocksley are signed in with sessions made here (their passwords are not needed);
the login tests use a throwaway author login that is deleted afterwards.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.pop("DL_ALLOW_WRITES", None)
os.environ["DL_COOKIE_SECURE"] = "0"   # the test client talks plain http

from digital_library.backend import auth, db  # noqa: E402
from digital_library.backend.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PUBS = ["authors", "titles", "titleauthor", "publishers", "pub_info", "employee", "jobs", "stores", "sales", "discounts", "roysched"]
ADMIN = {}    # {"Cookie": "dl_session=..."}, filled in by the client fixture
AUTHOR = {}
AUTHOR_BOOK = "PC9999"
TEST_LOGIN = "zz-test-login"
TEST_PASSWORD = "correct horse battery"


def session_for(username):
    """Sign a user in without their password (no last_login change); returns the cookie header."""
    token = auth.secrets.token_urlsafe(32)
    db.write_app("""INSERT INTO public.app_sessions (token_hash, user_id, expires_at)
                    SELECT %s, user_id, now() + interval '1 hour' FROM public.app_users WHERE username = %s""",
                 (auth._digest(token), username))
    return {"Cookie": f"{auth.COOKIE}={token}"}


def counts():
    return {t: db.one(f"SELECT count(*) AS n FROM public.{t}")["n"] for t in PUBS}


@pytest.fixture(scope="module")
def client():
    try:
        db.one("SELECT 1 AS ok")
    except Exception as exc:  # no database: nothing here can be checked
        pytest.skip(f"database not reachable: {exc}")
    ADMIN.update(session_for("admin"))
    AUTHOR.update(session_for("clocksley"))
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for h in (ADMIN, AUTHOR):
            auth.end_session(h["Cookie"].split("=", 1)[1])


@pytest.fixture(scope="module", autouse=True)
def pubs_tables_unchanged(client):
    before = counts()
    yield
    assert counts() == before, "the pubs tables changed during the tests"


# ---------------------------------------------------------------- who is calling
def test_needs_a_session(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"Cookie": f"{auth.COOKIE}=made-up"}).status_code == 401
    assert client.get("/api/dashboard", headers={"X-Demo-User": "admin"}).status_code == 401   # the old demo header is gone
    assert client.get("/api/users").status_code in (404, 405)


def test_me(client):
    assert client.get("/api/me", headers=ADMIN).json()["role"] == "admin"
    me = client.get("/api/me", headers=AUTHOR).json()
    assert (me["username"], me["au_id"]) == ("clocksley", "486-29-1786")


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/titles", "/api/sales", "/api/discounts", "/api/stores",
                                  "/api/publishers", "/api/employees", "/api/authors", "/api/removals"])
def test_admin_only(client, path):
    assert client.get(path, headers=AUTHOR).status_code == 403
    assert client.get(path, headers=ADMIN).status_code == 200


@pytest.mark.parametrize("path", ["/api/my/books", "/api/my/profile"])
def test_author_only(client, path):
    assert client.get(path, headers=ADMIN).status_code == 403
    assert client.get(path, headers=AUTHOR).status_code == 200


def test_author_sees_only_own_books(client):
    books = client.get("/api/my/books", headers=AUTHOR).json()
    assert {b["title_id"] for b in books} == {AUTHOR_BOOK}
    assert client.get(f"/api/titles/{AUTHOR_BOOK}", headers=AUTHOR).status_code == 200
    assert client.get("/api/titles/MC3021", headers=AUTHOR).status_code == 403


# ---------------------------------------------------------------- reads match the database
def test_dashboard_totals(client):
    t = client.get("/api/dashboard", headers=ADMIN).json()["totals"]
    expected = db.one("""SELECT count(DISTINCT (stor_id, ord_num)) AS orders, sum(qty) AS units FROM public.sales""")
    assert (t["orders"], t["units"]) == (expected["orders"], expected["units"])
    assert t["employees"] == db.one("SELECT count(*) AS n FROM public.employee")["n"]


def test_sales_paging_and_filters(client):
    all_lines = client.get("/api/sales?page_size=100", headers=ADMIN).json()
    assert len(all_lines["rows"]) == all_lines["totals"]["lines"]
    page2 = client.get("/api/sales?page=2&page_size=8", headers=ADMIN).json()
    assert page2["rows"] == all_lines["rows"][8:16]
    one_store = client.get("/api/sales?stor_id=8042&page_size=100", headers=ADMIN).json()
    assert one_store["rows"] and all(r["stor_id"] == "8042" for r in one_store["rows"])


def test_title_detail_flags_bad_royalty_shares(client):
    d = client.get("/api/titles/MC3021", headers=ADMIN).json()
    assert d["share_total"] == sum(a["royaltyper"] for a in d["authors"])
    assert d["title"]["title_id"] == "MC3021"
    assert client.get("/api/titles/XX0000", headers=ADMIN).status_code == 404


def test_employees_out_of_range(client):
    d = client.get("/api/employees?page_size=100", headers=ADMIN).json()
    flagged = [e for e in d["rows"] if not e["level_in_range"]]
    assert len(flagged) == d["totals"]["out_of_range"]
    assert all(not e["min_lvl"] <= e["job_lvl"] <= e["max_lvl"] for e in flagged)


def test_lookups(client):
    lk = client.get("/api/lookups", headers=ADMIN).json()
    assert lk["next_order"].startswith("N") and len(lk["next_order"]) == 5
    assert {"stores", "publishers", "titles", "authors", "jobs", "discounts", "types", "payterms"} <= lk.keys()


# ---------------------------------------------------------------- writes: checked, then rolled back
def test_new_order_is_checked_not_stored(client):
    order = {"stor_id": "8042", "ord_num": "TEST-0001", "ord_date": "2026-10-26", "qty": 3, "payterms": "Net 30", "title_id": "BU1032"}
    r = client.post("/api/sales", headers=ADMIN, json=order)
    assert r.status_code == 200 and r.json() == {"saved": False, "dry_run": True, "ord_num": "TEST-0001"}
    assert db.one("SELECT count(*) AS n FROM public.sales WHERE ord_num = 'TEST-0001'")["n"] == 0


def test_duplicate_order_line_is_rejected_by_the_database(client):
    existing = db.one("SELECT stor_id, ord_num, title_id FROM public.sales LIMIT 1")
    r = client.post("/api/sales", headers=ADMIN, json={**existing, "ord_date": "2026-10-26", "qty": 1, "payterms": "Net 30"})
    assert r.status_code == 409 and r.json()["constraint"] == "sales_pkey"


def test_unknown_store_is_rejected_by_the_database(client):
    r = client.post("/api/sales", headers=ADMIN, json={"stor_id": "0000", "ord_num": "TEST-0002", "ord_date": "2026-10-26",
                                                        "qty": 1, "payterms": "Net 30", "title_id": "BU1032"})
    assert r.status_code == 400 and r.json()["constraint"] == "sales_stor_id_fkey"


def test_form_field_errors(client):
    r = client.post("/api/discounts", headers=ADMIN, json={"discounttype": "Volume", "lowqty": 50, "highqty": 10, "discount": 2})
    assert r.status_code == 422 and "highqty" in r.json()["errors"]
    r = client.post("/api/employees", headers=ADMIN, json={"emp_id": "BAD", "fname": "A", "lname": "B", "pub_id": "9002", "job_id": 1, "job_lvl": 10})
    assert r.status_code == 422 and "emp_id" in r.json()["errors"]


def test_employee_level_must_fit_the_job(client):
    job = db.one("SELECT job_id, min_lvl, max_lvl FROM public.jobs WHERE max_lvl < 250 ORDER BY job_id LIMIT 1")
    body = {"emp_id": "TST12345F", "fname": "Test", "lname": "Person", "pub_id": "9002", "job_id": job["job_id"]}
    r = client.post("/api/employees", headers=ADMIN, json={**body, "job_lvl": job["max_lvl"] + 1})
    assert r.status_code == 422 and "job_lvl" in r.json()["errors"]
    r = client.post("/api/employees", headers=ADMIN, json={**body, "job_lvl": job["min_lvl"]})
    assert r.status_code == 200 and r.json()["dry_run"] is True


def test_add_book(client):
    book = {"title_id": "TS9999", "title": "Test Book", "type": "business", "pub_id": "1389", "price": "9.99"}
    assert client.post("/api/titles", headers=ADMIN, json=book).status_code == 422   # an admin must pick the author
    r = client.post("/api/titles", headers=ADMIN, json={**book, "au_id": "486-29-1786"})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    r = client.post("/api/titles", headers=AUTHOR, json={**book, "au_id": "172-32-1176"})
    assert r.json()["au_id"] == "486-29-1786"   # an author is always linked to themself
    assert db.one("SELECT count(*) AS n FROM public.titles WHERE title_id = 'TS9999'")["n"] == 0


def test_author_edits_only_own_books(client):
    fields = {"title": "Net Etiquette", "type": "popular_comp", "pub_id": "1389", "price": "14.99"}
    assert client.put(f"/api/titles/{AUTHOR_BOOK}", headers=AUTHOR, json=fields).json()["dry_run"] is True
    assert client.put("/api/titles/MC3021", headers=AUTHOR, json=fields).status_code == 403
    assert db.one("SELECT price FROM public.titles WHERE title_id = %s", (AUTHOR_BOOK,))["price"] is None


def test_new_publisher(client):
    r = client.post("/api/publishers", headers=ADMIN, json={"pub_id": "9004", "pub_name": "Riverstone Books", "pr_info": "Guides."})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert client.post("/api/publishers", headers=ADMIN, json={"pub_id": "0736", "pub_name": "Dup"}).status_code == 409


# ---------------------------------------------------------------- remove and restore (app table, really written)
def test_remove_and_restore(client):
    assert db.one("SELECT count(*) AS n FROM public.app_title_removals WHERE title_id = %s", (AUTHOR_BOOK,))["n"] == 0
    try:
        assert client.post("/api/titles/MC3021/remove", headers=AUTHOR).status_code == 403
        assert client.post(f"/api/titles/{AUTHOR_BOOK}/remove", headers=AUTHOR).json()["removed"] is True
        assert client.get("/api/my/books", headers=AUTHOR).json() == []
        assert AUTHOR_BOOK not in {t["title_id"] for t in client.get("/api/titles", headers=ADMIN).json()["rows"]}
        assert [r["title_id"] for r in client.get("/api/removals", headers=ADMIN).json()] == [AUTHOR_BOOK]
        assert client.delete(f"/api/removals/{AUTHOR_BOOK}", headers=AUTHOR).status_code == 403
        assert client.delete(f"/api/removals/{AUTHOR_BOOK}", headers=ADMIN).json()["restored"] is True
        assert [b["title_id"] for b in client.get("/api/my/books", headers=AUTHOR).json()] == [AUTHOR_BOOK]
    finally:
        db.write_app("DELETE FROM public.app_title_removals WHERE title_id = %s", (AUTHOR_BOOK,))


# ---------------------------------------------------------------- logins (app_users, really written; cleaned up)
@pytest.fixture
def spare_author(client):
    """An author with no login, and cleanup of the throwaway login made for them."""
    found = db.one("""SELECT a.au_id FROM public.authors a
                      WHERE NOT EXISTS (SELECT 1 FROM public.app_users u WHERE u.au_id = a.au_id) ORDER BY a.au_id LIMIT 1""")
    auth._fails.clear()
    yield found["au_id"]
    db.write_app("DELETE FROM public.app_users WHERE username = %s", (TEST_LOGIN,))
    auth._fails.clear()


def sign_in(client, password=TEST_PASSWORD, username=TEST_LOGIN):
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    client.cookies.clear()   # keep the shared client cookie-free; tests pass the cookie explicitly
    return r


def cookie(r):
    return {"Cookie": f"{auth.COOKIE}={r.cookies[auth.COOKIE]}"}


def test_only_the_admin_manages_logins(client, spare_author):
    body = {"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD}
    assert client.post("/api/logins", headers=AUTHOR, json=body).status_code == 403
    assert client.post("/api/logins", json=body).status_code == 401
    assert client.put("/api/logins/clocksley/password", headers=AUTHOR, json={"password": TEST_PASSWORD}).status_code == 403


def test_create_login_and_sign_in(client, spare_author):
    bad = client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": "abcd"})
    assert bad.status_code == 422 and "password" in bad.json()["errors"]
    taken = client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": "clocksley", "password": TEST_PASSWORD})
    assert taken.status_code == 409 and "username" in taken.json()["errors"]
    again = client.post("/api/logins", headers=ADMIN, json={"au_id": "486-29-1786", "username": TEST_LOGIN, "password": TEST_PASSWORD})
    assert again.status_code == 409   # clocksley's author already has a login
    r = client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD})
    assert r.status_code == 200 and r.json()["created"] is True
    stored = db.one("SELECT password_hash, role, created_by FROM public.app_users WHERE username = %s", (TEST_LOGIN,))
    assert stored["role"] == "author" and stored["password_hash"].startswith("$2") and TEST_PASSWORD not in stored["password_hash"]

    assert sign_in(client, "wrong password").status_code == 401
    assert sign_in(client, username="nobody-here").status_code == 401
    r = sign_in(client)
    assert r.status_code == 200 and r.json()["au_id"] == spare_author
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    me = cookie(r)
    assert client.get("/api/my/books", headers=me).status_code == 200
    assert client.get("/api/dashboard", headers=me).status_code == 403

    assert client.post("/api/logout", headers=me).status_code == 200
    assert client.get("/api/me", headers=me).status_code == 401


def test_password_reset_and_disable_end_sessions(client, spare_author):
    client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD})
    me = cookie(sign_in(client))
    new = "a brand new password"
    assert client.put(f"/api/logins/{TEST_LOGIN}/password", headers=ADMIN, json={"password": new}).status_code == 200
    assert client.get("/api/me", headers=me).status_code == 401
    assert sign_in(client).status_code == 401
    me = cookie(sign_in(client, new))

    wrong = client.post("/api/me/password", headers=me, json={"current_password": "nope", "password": TEST_PASSWORD})
    assert wrong.status_code == 422 and "current_password" in wrong.json()["errors"]
    assert client.post("/api/me/password", headers=me, json={"current_password": new, "password": TEST_PASSWORD}).status_code == 200
    assert client.get("/api/me", headers=me).status_code == 200   # the browser that changed it stays signed in

    assert client.put(f"/api/logins/{TEST_LOGIN}/active", headers=ADMIN, json={"is_active": False}).status_code == 200
    assert client.get("/api/me", headers=me).status_code == 401
    assert sign_in(client).status_code == 401
    assert client.put(f"/api/logins/{TEST_LOGIN}/active", headers=ADMIN, json={"is_active": True}).status_code == 200
    assert sign_in(client).status_code == 200
    assert client.put("/api/logins/admin/active", headers=ADMIN, json={"is_active": False}).status_code == 404   # authors only


def test_too_many_wrong_passwords(client, spare_author):
    client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD})
    for _ in range(auth.FAIL_LIMIT):
        assert sign_in(client, "wrong password").status_code == 401
    assert sign_in(client).status_code == 429


# ---------------------------------------------------------------- settings
def test_settings(client):
    admin = client.get("/api/settings", headers=ADMIN).json()
    assert admin["account"]["username"] == "admin" and admin["account"]["sessions"] >= 1
    assert admin["system"]["writes"] is False and admin["system"]["password_min"] == auth.PASSWORD_MIN
    author = client.get("/api/settings", headers=AUTHOR).json()
    assert author["account"]["role"] == "author" and "system" not in author   # server details are for the admin only
    assert client.get("/api/settings").status_code == 401


def test_sign_out_other_browsers(client, spare_author):
    client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD})
    here, there = cookie(sign_in(client)), cookie(sign_in(client))
    assert client.get("/api/settings", headers=here).json()["account"]["sessions"] == 2
    assert client.post("/api/me/sessions/end-others", headers=here).status_code == 200
    assert client.get("/api/me", headers=here).status_code == 200
    assert client.get("/api/me", headers=there).status_code == 401


# ---------------------------------------------------------------- library for authors (read-only catalog)
def test_author_browses_the_whole_library(client):
    lib = client.get("/api/library", headers=AUTHOR).json()
    visible = db.one("""SELECT count(*) AS n FROM public.titles t
                        WHERE NOT EXISTS (SELECT 1 FROM public.app_title_removals r WHERE r.title_id = t.title_id)""")["n"]
    assert lib["total"] == visible == len(lib["rows"])
    assert {r["title_id"] for r in lib["rows"] if r["mine"]} == {AUTHOR_BOOK}
    other = client.get("/api/library/MC3021", headers=AUTHOR)
    assert other.status_code == 200 and other.json()["mine"] is False and other.json()["authors"]
    assert not {"ytd_sales", "advance", "royalty", "royaltyper"} & other.json().keys()   # earnings stay with the admin
    assert client.get("/api/titles/MC3021", headers=AUTHOR).status_code == 403          # the full record is still admin-only
    assert client.get("/api/library/XX0000", headers=AUTHOR).status_code == 404
    assert client.get("/api/library").status_code == 401


def test_library_search_and_hidden_books(client):
    found = client.get("/api/library?q=etiquette", headers=AUTHOR).json()["rows"]
    assert [r["title_id"] for r in found] == [AUTHOR_BOOK]
    try:
        client.post(f"/api/titles/{AUTHOR_BOOK}/remove", headers=AUTHOR)
        assert client.get(f"/api/library/{AUTHOR_BOOK}", headers=ADMIN).status_code == 404
        assert AUTHOR_BOOK not in {r["title_id"] for r in client.get("/api/library", headers=ADMIN).json()["rows"]}
    finally:
        db.write_app("DELETE FROM public.app_title_removals WHERE title_id = %s", (AUTHOR_BOOK,))


# ---------------------------------------------------------------- account switcher (several accounts in one browser)
SECOND_LOGIN = "zz-test-login-2"


@pytest.fixture
def two_logins(client, spare_author):
    """Two throwaway author logins, and a fresh "browser" (its own cookie jar) to sign them in on."""
    second = db.one("""SELECT a.au_id FROM public.authors a
                       WHERE NOT EXISTS (SELECT 1 FROM public.app_users u WHERE u.au_id = a.au_id) AND a.au_id <> %s
                       ORDER BY a.au_id LIMIT 1""", (spare_author,))["au_id"]
    for au_id, name in ((spare_author, TEST_LOGIN), (second, SECOND_LOGIN)):
        client.post("/api/logins", headers=ADMIN, json={"au_id": au_id, "username": name, "password": TEST_PASSWORD})
    with TestClient(app) as browser:
        yield browser
    db.write_app("DELETE FROM public.app_users WHERE username = %s", (SECOND_LOGIN,))


def test_switch_between_signed_in_accounts(two_logins):
    browser = two_logins
    assert browser.get("/api/accounts").json() == []
    browser.post("/api/login", json={"username": TEST_LOGIN, "password": TEST_PASSWORD})
    browser.post("/api/login", json={"username": SECOND_LOGIN, "password": TEST_PASSWORD})   # "Add another account"
    listed = browser.get("/api/accounts").json()
    assert [(a["username"], a["active"]) for a in listed] == [(SECOND_LOGIN, True), (TEST_LOGIN, False)]
    assert browser.get("/api/me").json()["username"] == SECOND_LOGIN

    r = browser.post("/api/accounts/switch", json={"username": TEST_LOGIN})     # no password needed
    assert r.status_code == 200 and r.json()["username"] == TEST_LOGIN
    assert browser.get("/api/me").json()["username"] == TEST_LOGIN
    assert browser.post("/api/accounts/switch", json={"username": "clocksley"}).status_code == 404   # not signed in here

    # signing out the active account leaves the other one signed in and active
    out = browser.post("/api/logout").json()
    assert out["me"]["username"] == SECOND_LOGIN
    assert [a["username"] for a in browser.get("/api/accounts").json()] == [SECOND_LOGIN]
    assert browser.post("/api/accounts/switch", json={"username": TEST_LOGIN}).status_code == 404


def test_sign_out_of_all_accounts(two_logins):
    browser = two_logins
    for name in (TEST_LOGIN, SECOND_LOGIN):
        browser.post("/api/login", json={"username": name, "password": TEST_PASSWORD})
    tokens = [browser.cookies.get(auth.COOKIE), *browser.cookies.get(auth.OTHERS_COOKIE).split(".")]
    assert browser.post("/api/logout", json={"all": True}).json() == {"signed_out": True, "me": None}
    assert browser.get("/api/me").status_code == 401 and browser.get("/api/accounts").json() == []
    for t in tokens:   # the sessions are gone from the database, not just from the cookies
        assert auth.session_user(t) is None


def test_disabled_account_drops_out_of_the_switcher(client, two_logins):
    browser = two_logins
    for name in (TEST_LOGIN, SECOND_LOGIN):
        browser.post("/api/login", json={"username": name, "password": TEST_PASSWORD})
    client.put(f"/api/logins/{TEST_LOGIN}/active", headers=ADMIN, json={"is_active": False})
    assert [a["username"] for a in browser.get("/api/accounts").json()] == [SECOND_LOGIN]
    assert browser.post("/api/accounts/switch", json={"username": TEST_LOGIN}).status_code == 404


@pytest.mark.parametrize("q, expected", [("etiquette", "PC9999"), ("pc9999", "PC9999"), ("Locksley", "PC9999"),
                                         ("algodata", "PC9999"), ("computing", "PC9999"), ("  etiquette ", "PC9999")])
def test_search_matches_title_id_author_publisher_and_type(client, q, expected):
    for headers, path in ((ADMIN, "/api/titles"), (AUTHOR, "/api/library")):
        ids = {r["title_id"] for r in client.get(path, params={"q": q}, headers=headers).json()["rows"]}
        assert expected in ids, (path, q)


def test_search_with_no_match(client):
    assert client.get("/api/library", params={"q": "no such book zzz"}, headers=AUTHOR).json()["rows"] == []



# ---------------------------------------------------------------- admin adds a new author (authors row + login)
NEW_AUTHOR = {"au_fname": "Zed", "au_lname": "Testwriter", "phone": "555 010-0000", "city": "Austin", "state": "tx", "zip": "73301", "contract": True}


def test_add_author_is_admin_only_and_checked(client):
    body = {"author": NEW_AUTHOR, "login": {"username": TEST_LOGIN, "password": TEST_PASSWORD}}
    assert client.post("/api/authors", headers=AUTHOR, json=body).status_code == 403
    bad = client.post("/api/authors", headers=ADMIN, json={"author": {**NEW_AUTHOR, "zip": "7330", "au_fname": ""}})
    assert bad.status_code == 422 and {"zip", "au_fname"} <= bad.json()["errors"].keys()
    taken = client.post("/api/authors", headers=ADMIN, json={"author": NEW_AUTHOR, "login": {"username": "clocksley", "password": TEST_PASSWORD}})
    assert taken.status_code == 409 and "username" in taken.json()["errors"]
    dup = client.post("/api/authors", headers=ADMIN, json={"author": {**NEW_AUTHOR, "au_id": "486-29-1786"}})
    assert dup.status_code == 409 and "au_id" in dup.json()["errors"]


def test_add_author_in_practice_mode_stores_nothing(client):
    r = client.post("/api/authors", headers=ADMIN, json={"author": NEW_AUTHOR, "login": {"username": TEST_LOGIN, "password": TEST_PASSWORD}})
    out = r.json()
    assert r.status_code == 200 and out["dry_run"] is True and out["username"] == TEST_LOGIN
    assert db.one("SELECT count(*) AS n FROM public.authors WHERE au_id = %s", (out["au_id"],))["n"] == 0
    assert db.one("SELECT count(*) AS n FROM public.app_users WHERE username = %s", (TEST_LOGIN,))["n"] == 0


def test_add_author_live_then_they_sign_in(client, monkeypatch):
    monkeypatch.setattr(db, "ALLOW_WRITES", True)
    au_id = None
    try:
        out = client.post("/api/authors", headers=ADMIN, json={"author": NEW_AUTHOR, "login": {"username": TEST_LOGIN, "password": TEST_PASSWORD}}).json()
        au_id = out["au_id"]
        assert out["saved"] is True
        row = db.one("SELECT au_fname, state, contract FROM public.authors WHERE au_id = %s", (au_id,))
        assert (row["au_fname"], row["state"], row["contract"]) == ("Zed", "TX", True)
        r = sign_in(client)
        assert r.status_code == 200 and r.json()["au_id"] == au_id and r.json()["name"] == "Zed Testwriter"
        assert client.get("/api/my/books", headers=cookie(r)).json() == []
        assert au_id in {a["au_id"] for a in client.get("/api/authors", headers=ADMIN).json() if a["username"] == TEST_LOGIN}
    finally:
        db.write_app("DELETE FROM public.app_users WHERE username = %s", (TEST_LOGIN,))
        if au_id:
            db.write_app("DELETE FROM public.authors WHERE au_id = %s", (au_id,))


# ---------------------------------------------------------------- gaps: role checks on writes, sessions, 404s
@pytest.mark.parametrize("method, path", [("post", "/api/sales"), ("post", "/api/discounts"), ("post", "/api/publishers"),
                                          ("post", "/api/employees"), ("post", "/api/authors"), ("delete", f"/api/removals/{AUTHOR_BOOK}"),
                                          ("put", "/api/logins/clocksley/active")])
def test_admin_only_writes_reject_authors(client, method, path):
    kwargs = {} if method == "delete" else {"json": {}}
    assert getattr(client, method)(path, headers=AUTHOR, **kwargs).status_code == 403


@pytest.mark.parametrize("path", ["/api/lookups", "/api/library/MC3021", f"/api/titles/{AUTHOR_BOOK}", "/api/my/books"])
def test_reads_need_a_session(client, path):
    assert client.get(path).status_code == 401


def test_expired_session_is_rejected(client):
    token = auth.secrets.token_urlsafe(32)
    db.write_app("""INSERT INTO public.app_sessions (token_hash, user_id, created_at, expires_at)
                    SELECT %s, user_id, now() - interval '2 hours', now() - interval '1 minute' FROM public.app_users WHERE username = 'clocksley'""",
                 (auth._digest(token),))
    try:
        assert client.get("/api/me", headers={"Cookie": f"{auth.COOKIE}={token}"}).status_code == 401
    finally:
        auth.end_session(token)


def test_remember_me_sets_a_lasting_cookie(client, spare_author):
    client.post("/api/logins", headers=ADMIN, json={"au_id": spare_author, "username": TEST_LOGIN, "password": TEST_PASSWORD})
    client.cookies.clear()
    short = client.post("/api/login", json={"username": TEST_LOGIN, "password": TEST_PASSWORD})
    client.cookies.clear()
    long = client.post("/api/login", json={"username": TEST_LOGIN, "password": TEST_PASSWORD, "remember": True})
    client.cookies.clear()
    def session_cookie(r):
        return next(h.lower() for h in r.headers.get_list("set-cookie") if h.startswith(f"{auth.COOKIE}="))
    assert "max-age" not in session_cookie(short)             # ends with the browser
    age = int(session_cookie(long).split("max-age=")[1].split(";")[0])
    assert auth.REMEMBER_DAYS * 86400 - 60 <= age <= auth.REMEMBER_DAYS * 86400


def test_missing_book_and_restore_are_404(client):
    assert client.post("/api/titles/XX0000/remove", headers=ADMIN).status_code == 404
    assert client.put("/api/titles/XX0000", headers=ADMIN, json={"title": "T", "type": "business", "pub_id": "1389"}).status_code == 404
    assert client.delete(f"/api/removals/{AUTHOR_BOOK}", headers=ADMIN).status_code == 404   # not removed


def test_blank_strings_reach_the_database_as_null(client):
    fields = {"title": "Net Etiquette", "type": "popular_comp", "pub_id": "1389", "price": "", "notes": "   "}
    r = client.put(f"/api/titles/{AUTHOR_BOOK}", headers=AUTHOR, json=fields)
    assert r.status_code == 200 and r.json()["dry_run"] is True
