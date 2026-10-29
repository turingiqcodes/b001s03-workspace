"""Passwords and sessions.

Passwords are stored as bcrypt hashes in app_users.password_hash. Signing in creates a row in
app_sessions and sets an HttpOnly cookie holding a random token; the table only keeps the
token's SHA-256. Logging out, a password change or disabling a login deletes the rows.
"""
import hashlib
import os
import secrets
import threading
import time
from datetime import timedelta

import bcrypt

from . import db

COOKIE = "dl_session"
# Other accounts signed in on the same browser (the account switcher): their tokens, "."-separated.
OTHERS_COOKIE = "dl_others"
MAX_ACCOUNTS = 5
# Browsers treat http://localhost as secure, so this only needs turning off for plain-http hosts.
COOKIE_SECURE = os.environ.get("DL_COOKIE_SECURE", "1") != "0"
SESSION_HOURS = 12
REMEMBER_DAYS = 30
PASSWORD_MIN = 5
PASSWORD_MAX_BYTES = 72          # bcrypt ignores anything longer

# Checked when the username does not exist, so a wrong username takes as long as a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"not a real password", bcrypt.gensalt()).decode()


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def password_problem(password):
    """A message for the form, or None when the password is acceptable."""
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password.encode()) > PASSWORD_MAX_BYTES:
        return f"Use at most {PASSWORD_MAX_BYTES} bytes."
    return None


def check_login(username, password):
    """The active user for these credentials, or None."""
    user = db.one("SELECT user_id, password_hash FROM public.app_users WHERE username = %s AND is_active",
                  (username,))
    stored = user["password_hash"] if user and user["password_hash"] else _DUMMY_HASH
    ok = bcrypt.checkpw(password.encode()[:PASSWORD_MAX_BYTES], stored.encode())
    return user if ok and user and user["password_hash"] else None


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id, remember=False):
    """Returns (token, max_age); max_age is None for a cookie that ends with the browser."""
    token = secrets.token_urlsafe(32)
    lifetime = timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)
    with db.cursor(commit=True) as cur:
        cur.execute("DELETE FROM public.app_sessions WHERE expires_at < now()")
        cur.execute("INSERT INTO public.app_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s)",
                    (_digest(token), user_id, lifetime))
        cur.execute("UPDATE public.app_users SET last_login = now() WHERE user_id = %s", (user_id,))
    return token, int(lifetime.total_seconds()) if remember else None


def session_user(token):
    if not token:
        return None
    return db.one("""SELECT u.user_id, u.username, u.role, u.au_id, a.au_fname || ' ' || a.au_lname AS name
                     FROM public.app_sessions s JOIN public.app_users u USING (user_id)
                     LEFT JOIN public.authors a USING (au_id)
                     WHERE s.token_hash = %s AND s.expires_at > now() AND u.is_active""", (_digest(token),))


def cookie_age(token):
    """Seconds the cookie should live: the session's time left if "Remember me" was ticked, else None (browser session)."""
    row = db.one("""SELECT extract(epoch FROM expires_at - now())::int AS left_s,
                           expires_at - created_at > %s AS remembered
                    FROM public.app_sessions WHERE token_hash = %s""", (timedelta(hours=SESSION_HOURS), _digest(token)))
    return row["left_s"] if row and row["remembered"] else None


def end_session(token):
    if token:
        db.write_app("DELETE FROM public.app_sessions WHERE token_hash = %s", (_digest(token),))


def end_all_sessions(user_id, keep_token=None):
    db.write_app("DELETE FROM public.app_sessions WHERE user_id = %s AND token_hash <> %s",
                 (user_id, _digest(keep_token) if keep_token else ""))


# ---------------------------------------------------------------- too many wrong passwords
FAIL_LIMIT = 5
FAIL_WINDOW = 15 * 60
_fails = {}
_fails_lock = threading.Lock()


def locked_out(key):
    now = time.monotonic()
    with _fails_lock:
        recent = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
        _fails[key] = recent
        return len(recent) >= FAIL_LIMIT


def record_failure(key):
    with _fails_lock:
        _fails.setdefault(key, []).append(time.monotonic())


def clear_failures(key):
    with _fails_lock:
        _fails.pop(key, None)
