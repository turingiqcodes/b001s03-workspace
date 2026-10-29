"""Postgres access for the Digital Library API.

Connection settings come from the usual PG* environment variables (defaults: the pubs RDS
database the DAG loads). The password is never in code: libpq reads PGPASSWORD or ~/.pgpass.

Writes to the 11 pubs tables are dry-run unless DL_ALLOW_WRITES=1: the statements really run,
so every constraint and foreign key is checked, and then the transaction is rolled back.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

CONNECT = {
    "host": os.environ.get("PGHOST", "turingiq-training-rds-pubs-b001.cx0ywsig8do8.us-east-1.rds.amazonaws.com"),
    "port": int(os.environ.get("PGPORT", "5432")),
    "dbname": os.environ.get("PGDATABASE", "pubs_b001s03"),
    "user": os.environ.get("PGUSER", "b001s03"),
    "sslmode": os.environ.get("PGSSLMODE", "require"),
    "connect_timeout": 10,
    "application_name": "digital-library-api",
}
ALLOW_WRITES = os.environ.get("DL_ALLOW_WRITES") == "1"

_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, int(os.environ.get("DL_DB_POOL", "5")), **CONNECT)
    return _pool


def close():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def cursor(commit=False):
    """A dict cursor in its own transaction; rolled back unless commit is True."""
    conn = pool().getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool().putconn(conn)


def rows(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def exists(sql, params=None):
    """True when the query returns at least one row."""
    return one(sql, params) is not None


def write_pubs(statements):
    """Run (sql, params) pairs in one transaction against the pubs tables.

    Returns True when committed, False when it was a dry run (checked, then rolled back).
    """
    with cursor(commit=ALLOW_WRITES) as cur:
        for sql, params in statements:
            cur.execute(sql, params)
    return ALLOW_WRITES


def write_app(sql, params=None):
    """Write to the app's own tables (app_users, app_title_removals); always committed."""
    with cursor(commit=True) as cur:
        cur.execute(sql, params)
        return cur.rowcount
