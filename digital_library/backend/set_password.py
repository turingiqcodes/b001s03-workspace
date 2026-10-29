"""Set a login's password from the command line (how the admin gets the first password).

Run from the repo root and type the password at the prompt; it is never shown or stored in
shell history:
    digital_library/.venv/bin/python -m digital_library.backend.set_password admin
"""
import getpass
import sys

from . import auth, db


def main(argv):
    if len(argv) != 1:
        sys.exit("usage: python -m digital_library.backend.set_password <username>")
    username = argv[0]
    user = db.one("SELECT user_id, role, is_active FROM public.app_users WHERE username = %s", (username,))
    if not user:
        sys.exit(f"No login called {username!r} in app_users.")
    password = getpass.getpass(f"New password for {username}: ")
    problem = auth.password_problem(password)
    if problem:
        sys.exit(problem)
    if getpass.getpass("Type it again: ") != password:
        sys.exit("The two passwords are different. Nothing was changed.")
    db.write_app("UPDATE public.app_users SET password_hash = %s WHERE user_id = %s",
                 (auth.hash_password(password), user["user_id"]))
    auth.end_all_sessions(user["user_id"])
    state = "" if user["is_active"] else " (this login is disabled, so it still cannot sign in)"
    print(f"Password set for {username} ({user['role']}){state}.")


if __name__ == "__main__":
    main(sys.argv[1:])
