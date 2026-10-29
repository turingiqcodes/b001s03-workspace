-- App tables for the Bookworm screens. These sit beside the 11 pubs tables
-- and never change or delete rows in them.
--
--   app_users           logins. Only an admin creates them; there is no sign-up.
--                       The admin's own password is set from the command line:
--                       digital_library/.venv/bin/python -m digital_library.backend.set_password admin
--   app_sessions        who is signed in (the app's session cookie).
--   app_title_removals  "Delete" on My Books. The title is hidden in the app,
--                       but its rows in titles / titleauthor / sales / roysched
--                       stay, so the DAG reload does not bring it back either.
--
-- Safe to run more than once.

BEGIN;

CREATE TABLE IF NOT EXISTS public.app_users (
    user_id       serial PRIMARY KEY,
    username      varchar(40) NOT NULL UNIQUE,
    -- NULL until the admin sets a password ("Set password" on Author Access).
    -- The app stores a bcrypt hash here, never the plain password.
    password_hash text,
    role          varchar(10) NOT NULL CHECK (role IN ('admin', 'author')),
    -- Every author login belongs to exactly one row in authors; admins have none.
    au_id         varchar(11) UNIQUE REFERENCES public.authors (au_id),
    is_active     boolean NOT NULL DEFAULT true,
    created_by    integer REFERENCES public.app_users (user_id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login    timestamptz,
    CONSTRAINT app_users_author_link CHECK ((role = 'author') = (au_id IS NOT NULL))
);

-- One row per signed-in browser. The cookie holds a random token; only its SHA-256 is
-- stored, so a copy of this table cannot be used to sign in. Logging out, a password
-- change or disabling the login deletes the rows.
CREATE TABLE IF NOT EXISTS public.app_sessions (
    token_hash  char(64) PRIMARY KEY,
    user_id     integer NOT NULL REFERENCES public.app_users (user_id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS app_sessions_user_id ON public.app_sessions (user_id);

CREATE TABLE IF NOT EXISTS public.app_title_removals (
    title_id   varchar(6) PRIMARY KEY REFERENCES public.titles (title_id),
    removed_by integer NOT NULL REFERENCES public.app_users (user_id),
    removed_at timestamptz NOT NULL DEFAULT now()
);

-- The admin, then the one author who can log in for now: Charlene Locksley.
INSERT INTO public.app_users (username, role)
VALUES ('admin', 'admin')
ON CONFLICT (username) DO NOTHING;

INSERT INTO public.app_users (username, role, au_id, created_by)
SELECT 'clocksley', 'author', '486-29-1786', user_id
FROM public.app_users
WHERE username = 'admin'
ON CONFLICT (username) DO NOTHING;

COMMIT;

-- Queries the screens run --------------------------------------------------

-- Login: only active accounts get in; role picks the home screen
-- (admin -> Admin Dashboard, author -> My Books).
--   SELECT user_id, role, au_id, password_hash
--   FROM public.app_users
--   WHERE username = :username AND is_active;

-- My Books: titles the logged-in author is on, minus the ones they removed.
--   SELECT t.*
--   FROM public.titleauthor ta
--   JOIN public.titles t USING (title_id)
--   WHERE ta.au_id = :au_id
--     AND NOT EXISTS (SELECT 1 FROM public.app_title_removals r WHERE r.title_id = t.title_id);

-- Delete on My Books (only for a title the author is on):
--   INSERT INTO public.app_title_removals (title_id, removed_by)
--   SELECT ta.title_id, :user_id
--   FROM public.titleauthor ta
--   WHERE ta.title_id = :title_id AND ta.au_id = :au_id
--   ON CONFLICT (title_id) DO NOTHING;

-- Admin restores a removed book:
--   DELETE FROM public.app_title_removals WHERE title_id = :title_id;
