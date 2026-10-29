"""Unit tests that need no database, so they run even when RDS is unreachable.

    cd app && .venv/bin/python -m pytest -q tests/test_units.py
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from digital_library.backend import auth  # noqa: E402
from digital_library.backend.main import _field, search_sql  # noqa: E402
from digital_library.backend.models import BookIn, DiscountIn, LoginIn, NewLoginIn, PublisherIn  # noqa: E402

BOOK = {"title_id": "PC1234", "title": "A book", "type": "business", "pub_id": "1389"}


# ---------------------------------------------------------------- form models
def test_blank_optional_fields_become_none():
    book = BookIn(**BOOK, notes="   ", price="")
    assert (book.notes, book.price) == (None, None)


def test_strings_are_stripped():
    assert BookIn(**{**BOOK, "title": "  Padded  "}).title == "Padded"


@pytest.mark.parametrize("field, value", [("title_id", "pc1234"), ("title_id", "PC123"), ("pub_id", "13a9"),
                                          ("type", "poetry"), ("royalty", 101), ("price", "-1")])
def test_book_rejects_bad_values(field, value):
    with pytest.raises(ValidationError) as exc:
        BookIn(**{**BOOK, field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        BookIn(**BOOK, ytd_sales=10)


def test_discount_range_must_be_in_order():
    assert DiscountIn(discounttype="Bulk", lowqty=10, highqty=10, discount=5).highqty == 10
    with pytest.raises(ValidationError, match="at least the low quantity"):
        DiscountIn(discounttype="Bulk", lowqty=10, highqty=5, discount=5)


def test_publisher_state_is_two_letters():
    with pytest.raises(ValidationError):
        PublisherIn(pub_id="9999", pub_name="P", state="CAL")


def test_new_login_username_is_lowercased_and_checked():
    assert NewLoginIn(au_id="123-45-6789", username="  JSmith ", password="secret").username == "jsmith"
    for bad in ("ab", "-leading", "has space", "x" * 41):
        with pytest.raises(ValidationError):
            NewLoginIn(au_id="123-45-6789", username=bad, password="secret")


def test_login_keeps_password_whitespace():
    assert LoginIn(username="a", password=" pw ").password == " pw "


# ---------------------------------------------------------------- passwords and lockout
def test_password_rules():
    assert auth.password_problem("a" * (auth.PASSWORD_MIN - 1))
    assert auth.password_problem("a" * auth.PASSWORD_MIN) is None
    assert auth.password_problem("a" * auth.PASSWORD_MAX_BYTES) is None
    assert auth.password_problem("a" * (auth.PASSWORD_MAX_BYTES + 1))
    assert auth.password_problem("é" * 37)          # 74 bytes, though only 37 characters


def test_hash_round_trip():
    import bcrypt
    hashed = auth.hash_password("correct horse")
    assert hashed != "correct horse" and bcrypt.checkpw(b"correct horse", hashed.encode())


def test_lockout_after_limit_and_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    key = ("1.2.3.4", "unit-test-user")
    auth.clear_failures(key)
    for _ in range(auth.FAIL_LIMIT - 1):
        auth.record_failure(key)
    assert not auth.locked_out(key)
    auth.record_failure(key)
    assert auth.locked_out(key)
    now[0] += auth.FAIL_WINDOW
    assert not auth.locked_out(key)


def test_lockout_is_per_key(monkeypatch):
    monkeypatch.setattr(auth.time, "monotonic", lambda: 0.0)
    a, b = ("ip", "unit-a"), ("ip", "unit-b")
    auth.clear_failures(a), auth.clear_failures(b)
    for _ in range(auth.FAIL_LIMIT):
        auth.record_failure(a)
    assert auth.locked_out(a) and not auth.locked_out(b)
    auth.clear_failures(a)
    assert not auth.locked_out(a)


# ---------------------------------------------------------------- helpers
def test_search_sql_is_parameterised():
    sql, params = search_sql("'; DROP TABLE titles; --")
    assert "DROP" not in sql
    assert params[0] == "%'; DROP TABLE titles; --%"


@pytest.mark.parametrize("q, types", [("cook", ["mod_cook", "trad_cook"]), ("Computing", ["popular_comp"]),
                                      ("  business ", ["business"]), ("zzz", [])])
def test_search_sql_matches_type_labels(q, types):
    assert sorted(search_sql(q)[1][2]) == types


def test_field_name_from_error_location():
    assert _field(("body", "author", "au_fname")) == "au_fname"
    assert _field(("query", "page")) == "page"
    assert _field(("body",)) == "form"


# ---------------------------------------------------------------- backend and frontend agree
def test_frontend_type_labels_match_backend():
    import re

    from digital_library.backend.models import TYPE_LABEL
    src = (Path(__file__).resolve().parents[1] / "frontend" / "lib.js").read_text()
    body = re.search(r"export const TYPE_LABEL = \{(.*?)\};", src, re.S).group(1)
    js = {k: v for k, v in re.findall(r"(\w+): '([^']*)'", body)}
    assert js == TYPE_LABEL, "frontend/lib.js TYPE_LABEL has drifted from backend/models.py"


def test_lookup_payterms_follow_the_model():
    from typing import get_args

    from digital_library.backend.models import PAYTERM_VALUES, PAYTERMS
    assert PAYTERM_VALUES == list(get_args(PAYTERMS)) == ["Net 30", "Net 60", "ON invoice"]


# ---------------------------------------------------------------- database errors become clean responses
def _db_response(exc):
    import asyncio
    import json

    from digital_library.backend.main import database_error
    r = asyncio.run(database_error(None, exc))
    return r.status_code, json.loads(r.body)


@pytest.mark.parametrize("exc, status", [("UniqueViolation", 409), ("ForeignKeyViolation", 400), ("CheckViolation", 400),
                                         ("NotNullViolation", 400), ("NumericValueOutOfRange", 400)])
def test_rejected_writes_map_to_client_errors(exc, status):
    import psycopg2.errors
    code, body = _db_response(getattr(psycopg2.errors, exc)())
    assert code == status and set(body) == {"detail", "constraint"}   # Postgres's own message stays in the log


def test_unreachable_database_is_503_and_other_errors_500():
    import psycopg2
    import psycopg2.errors
    assert _db_response(psycopg2.OperationalError())[0] == 503
    assert _db_response(psycopg2.errors.UndefinedColumn())[0] == 500   # a SQL bug is not "the database is down"


@pytest.mark.xfail(strict=True, reason="BUG: PoolError is not an OperationalError, so a full pool answers 500, not 503")
def test_exhausted_pool_is_503():
    import psycopg2.pool
    assert _db_response(psycopg2.pool.PoolError("connection pool exhausted"))[0] == 503


@pytest.mark.xfail(strict=True, reason="BUG: a cleared Country field becomes None, which fails str instead of taking the USA default")
def test_blank_publisher_country_defaults_to_usa():
    assert PublisherIn(pub_id="9004", pub_name="X", country="").country == "USA"
