# Deploying the Google Apps Script bridge

The bridge in `apps_script/` is the only part that touches your Google account. It
runs inside Google as a Web App, as you, and the local backend calls it with a
shared secret.

You need a Google account and about 10 minutes.

## 1. Create the project

1. Open <https://script.google.com> and click **New project**. Rename it, e.g. *Email Manager Bridge*.
2. Open **Project Settings** (gear icon) and tick **Show "appsscript.json" manifest file in editor**.
3. Back in **Editor**:
   - Open `appsscript.json` and replace its content with `apps_script/appsscript.json`.
   - Rename `Code.gs` and paste in `apps_script/Code.gs`.
   - Add the files `Response`, `Validation`, `Gmail`, `Calendar` and `Drive` with **+ → Script**, and paste in the matching `.gs` files.
4. Click **Save**.

*Alternative with clasp:* `npm install -g @google/clasp`, then `clasp login`, then
`cd apps_script && clasp create --type webapp --title "Email Manager Bridge" --rootDir .`,
then `clasp push`. `.claspignore` keeps the tests out. Don't commit the generated `.clasp.json`.

## 2. Create the shared secret

The secret is kept in your local `.env`. Apps Script can't read that file, so you paste the
same value into the project's Script properties. Never write it into the `.gs` files, which
are committed to git.

1. Generate a random secret on your computer:
   `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Put it in `.env` as `APPS_SCRIPT_SHARED_SECRET=<value>`.
3. In Apps Script, open **Project Settings → Script properties → Add script property**.
   Set the property to `BRIDGE_SHARED_SECRET` and the value to the same secret, then click **Save script properties**.
4. In the editor, choose the function **checkSetup** in the toolbar and click **Run**.
5. Google asks you to authorize the project. It's your own script, so if you see "Google hasn't verified this app", choose **Advanced → Go to Email Manager Bridge (unsafe)**. Approve the scopes:
   - Gmail (`https://mail.google.com/`): read, send, label
   - Calendar: read and change events
   - Drive: search, create folders, save files
   - your email address (shown in the status bar)
6. Open **Execution log**. It should say `BRIDGE_SHARED_SECRET is set (64 characters)`.

To rotate the secret, generate a new one and update both `.env` and the script property.

## 3. Deploy as a Web App

1. Click **Deploy → New deployment**, choose the type **Web app** (gear icon next to "Select type").
2. Set **Execute as: Me** and **Who has access: Anyone**.
   - "Anyone" is needed because the backend calls without a Google login. Every request
     must carry the shared secret, and requests without it are refused.
   - Some Google Workspace organisations don't allow "Anyone". If yours doesn't, ask an admin
     or use a personal account.
3. Click **Deploy** and copy the **Web app URL** (it ends in `/exec`) into `.env` as `APPS_SCRIPT_URL`.

## 4. Check it

Start the backend and frontend (see README), then click **Check connection** in the app.
It should show *Google connected (your@address)*.

From a terminal, with the backend running:

```bash
curl "http://127.0.0.1:8000/api/status?check_bridge=true"
```

`"reachable": true` means the backend reached the bridge and the secret matched.

## Updating the code later

After changing a `.gs` file: **Deploy → Manage deployments → ✏️ Edit → Version: New version → Deploy**.
The URL stays the same. If you add a scope to `appsscript.json`, run any function once
in the editor to re-authorize.

## What the bridge will and won't do

- Every request is checked for the shared secret, a known operation and valid parameters.
- Gmail: search, read, list labels, draft, send, reply, reply draft, forward, add/remove existing labels, mark read/unread.
- Calendar (default calendar only): list, get, create, update, cancel. Recurring events are refused.
- Drive: search, list, file info, create folder (reuses an existing one with the same name), move file, save attachments into a folder (skips oversized, unsupported and duplicate files).
- No delete, trash or sharing operations exist.

## Bridge tests (no Google account needed)

```bash
cd apps_script
node --test tests/*.test.js
```

These run the real `.gs` files in Node with fake Gmail/Calendar/Drive services. They
check routing, the secret check, validation, query escaping and attachment rules. They
can't prove Google's live behaviour; for that, use the manual checks in the README.
