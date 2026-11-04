# Email Manager Agent

A local, single-user assistant for **Gmail, Google Calendar and Google Drive**. You give it
requests in plain language ("find unread emails from today", "move my 3pm to Thursday",
"save the invoice attachments to Drive"), a language model decides which tools to use, and
**nothing is sent, scheduled or changed until you approve it** in the app.

The model runs **locally with [Ollama](https://ollama.com)** by default (free, and your
email text stays on your computer). You can switch to the Claude API with `LLM_PROVIDER=claude`, or to Groq's free tier (or any
OpenAI-compatible API) with `LLM_PROVIDER=openai`. With either API, email text is sent to that provider.

> The SRS v1.0 was not available when this was built. Values the SRS would normally fix
> (step limits, timeouts, working hours, attachment size, …) are documented as **assumed**
> in [docs/REQUIREMENTS_CHECKLIST.md](docs/REQUIREMENTS_CHECKLIST.md). All of them can be
> changed in `.env`.

## How it works

```
Browser (React, localhost:5173)
   │  /api/*  (Vite proxy)
   ▼
FastAPI backend (127.0.0.1:8000) ── Ollama on this computer (default), Claude API or Groq/OpenAI-compatible API
   │  runs read tools now; turns write tools into pending approvals
   ▼
Google Apps Script Web App (runs as you, shared secret)
   ▼
Gmail · Calendar · Drive
```

- The **backend** owns the agent loop. The model only picks tools; the backend validates every call and runs it.
- **Read tools** (search, read, list, find free slots) run immediately.
- **Write tools** (send, reply, forward, drafts, labels, create/move/cancel events, folders, moves, saving attachments) are validated and stored as a *pending operation*. The browser shows the details, and you approve or reject. `POST /api/confirm` runs the stored parameters exactly once.
- The bridge secret (and the Claude key, if used) stay in the backend's `.env`. The browser never sees them.

```
email_manager_agent/
├── backend/            FastAPI app (Python 3.10+)
│   ├── app/
│   │   ├── main.py         app, localhost guards, error handlers
│   │   ├── config.py       all settings and limits (.env)
│   │   ├── api/routes.py   /api/status, /api/prompt, /api/confirm, /api/reset
│   │   ├── schemas.py      request/response models
│   │   ├── agent/          Ollama, Claude and OpenAI-compatible (Groq) clients, system prompt, tool registry, agent loop
│   │   ├── services/       bridge client, Gmail, Calendar, Drive, availability logic
│   │   ├── approvals.py    pending-operation lifecycle
│   │   └── sessions.py     in-memory sessions
│   ├── run.py          start the server with BACKEND_HOST/PORT
│   └── tests/          pytest suite (model and Google faked)
├── frontend/           React + TypeScript + Vite
│   └── src/            api/, state/, components/, types/, __tests__/
├── apps_script/        Google Apps Script bridge (+ Node tests)
└── docs/               requirements checklist, Apps Script setup
```

## Setup

Requirements: **Python 3.10+**, **Node.js 18+** (20 or 22 recommended), a Google account,
and **Ollama** (or an Anthropic API key if you choose Claude).

### 0. Install Ollama and a model

1. Install Ollama from <https://ollama.com/download> (Windows, macOS, Linux). It runs in the background on port 11434.
2. Pull a model that **supports tool calling**:

   ```bash
   ollama pull qwen3:8b        # recommended: about 5 GB download, needs ~8 GB free RAM
   # smaller machines: ollama pull qwen3:4b   or   ollama pull llama3.2:3b
   ```

3. Check it works: `ollama run qwen3:8b "say hi"`.

A GPU makes it much faster. On CPU only, each step can take tens of seconds, and one
request can use several steps.

### 1. Configure environment variables

```bash
cd email_manager_agent
cp .env.example .env
```

Edit `.env`:

| Variable | Required | What it is |
|---|---|---|
| `LLM_PROVIDER` | no | `ollama` (default), `claude`, or `openai` (Groq or another OpenAI-compatible API) |
| `OLLAMA_MODEL` | yes, for Ollama | The model you pulled, e.g. `qwen3:8b`. There is no built-in default. |
| `OLLAMA_BASE_URL` | no | Default `http://127.0.0.1:11434` |
| `OLLAMA_NUM_CTX` | no | Context window, default 16384 tokens. Ollama's own default is too small for this app. |
| `OLLAMA_TIMEOUT_SECONDS` | no | Default 600. Raise it on slow machines. |
| `OLLAMA_KEEP_ALIVE` | no | How long the model stays loaded between requests (default `30m`). Follow-up requests are much faster while it stays loaded. |
| `OLLAMA_THINK` | no | For thinking models such as qwen3: `false` is faster. Leave unset for other models. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | only for `openai` | For Groq: key from console.groq.com/keys, model with tool calling, e.g. `openai/gpt-oss-120b` |
| `OPENAI_BASE_URL` | no | Default `https://api.groq.com/openai/v1` (Groq). Use `https://api.openai.com/v1` for OpenAI. |
| `OPENAI_MAX_TOKENS`, `OPENAI_TIMEOUT_SECONDS` | no | Reply length limit (default 4096) and timeout (default 120) |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | only for Claude | Key from console.anthropic.com, model id e.g. `claude-opus-5-5` |
| `APPS_SCRIPT_URL` | yes | Web App URL ending in `/exec` (step 2) |
| `APPS_SCRIPT_SHARED_SECRET` | yes | Random secret you generate (step 2); also set as the `BRIDGE_SHARED_SECRET` script property |
| `USER_TIMEZONE` | recommended | IANA name, e.g. `Asia/Kolkata`. Used for working hours and times without an offset |
| `CLAUDE_EFFORT`, `CLAUDE_REFUSAL_FALLBACK` | no | Claude only: effort `low`…`max` (default `medium`); server-side fallback on refusals (default `true`) |
| everything else | no | Limits and working hours; see comments in `.env.example` and the checklist |

List values use JSON, e.g. `WORK_DAYS=[0,1,2,3,4]`.

### 2. Deploy the Google Apps Script bridge

Follow **[docs/APPS_SCRIPT_SETUP.md](docs/APPS_SCRIPT_SETUP.md)**: create the project,
paste the files, put your secret in `.env` and in the `BRIDGE_SHARED_SECRET` script property,
run `checkSetup`, then deploy as a Web App (*Execute as: Me*, *Who has access: Anyone*).
Copy the URL into `.env`.

### 3. Start the backend

```bash
cd email_manager_agent
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend
../.venv/bin/python run.py          # serves http://127.0.0.1:8000
```

(Windows: `.venv\Scripts\python` instead of `.venv/bin/python`.)

### 4. Start the frontend

In a second terminal:

```bash
cd email_manager_agent/frontend
npm install
npm run dev                          # serves http://localhost:5173
```

### 5. Open the app

Go to **<http://localhost:5173>**. The status bar shows whether the model and the Google
bridge are configured. Click **Check connection** to ping the model (for Ollama, it also checks the model is
pulled) and Google.

## Using it

Try:

- *Find unread emails from today*
- *Summarise the latest email from Alice*
- *Draft a reply to that email saying I'll send the report Friday*
- *Find a free 45 minute slot on Tuesday or Wednesday*
- *Schedule "Planning" with bob@example.com at the first free slot*
- *Move my 3pm meeting tomorrow to 4pm* (keeps the duration)
- *Find PDFs with "invoice" in the name*
- *Save the attachments from the latest invoice email to Drive*

When the model proposes a write, a card appears under **Waiting for your approval**:

- **Approve** runs exactly what's shown. **Reject** cancels it.
- If there are warnings (a calendar conflict, time outside working hours, skipped attachments, a repeat of something already done), tick **I have reviewed these warnings** first.
- Approvals expire after 10 minutes and can only be used once.
- **New conversation** clears the history and any pending approvals.

Sessions are kept in memory only. Restarting the backend forgets them.

## Tests

```bash
# Backend: 154 tests, no network (Ollama, Claude, Groq and Google are faked)
cd email_manager_agent/backend && ../.venv/bin/python -m pytest -q

# Frontend: 18 tests, typecheck and production build
cd email_manager_agent/frontend && npm test && npm run typecheck && npm run build

# Apps Script bridge: 15 tests in Node with fake Google services
cd email_manager_agent/apps_script && node --test tests/*.test.js
```

The automated tests never send real email or change your calendar or Drive.

### Manual check with your real account (after setup)

Use a test email address you own.

1. *Find my 3 most recent emails*: check the results against Gmail.
2. *Send a test email to <your address> saying hello*, then **Approve**, and check it arrives.
3. *Find a free 30 minute slot tomorrow*: check it against Google Calendar.
4. *Create an event "Agent test" at that time*, then **Approve**. Then *cancel the Agent test event*, then **Approve**.
5. *Find files named <something you have>* in Drive.

## API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/status?session_id=&check_bridge=` | none | Model (`llm`) and bridge configuration and issues, limits, the session's pending operations. `check_bridge=true` also pings the model provider and Google |
| `POST /api/prompt` | `{"session_id"?: str, "prompt": str}` | `{session_id, reply, steps, stop_reason, tool_calls[], pending_operations[]}` |
| `POST /api/confirm` | `{"session_id", "operation_id", "decision": "approve"\|"reject", "acknowledge_warnings"?: bool}` | `{status: executed\|rejected\|failed, message, result, pending_operations[]}` |
| `POST /api/reset` | `{"session_id"?: str}` | `{session_id, status: "reset"}` |

Errors are always `{"error": {"code": "...", "message": "..."}}`, for example `validation_error`
(422), `not_found` (404), `approval_error` / `acknowledgement_required` / `session_busy`
(409), `approval_expired` (410), `configuration_error` (503), `bridge_error` (502),
`bridge_timeout` (504) and `model_error` (502/429/504).

## Security

- **Local only.** `run.py` refuses non-loopback hosts. The app also rejects requests whose `Host` isn't `localhost`/`127.0.0.1` or that come from another machine, and CORS only allows the local frontend.
- **Approval is enforced by the backend, not by the model.** Write tools never run inside the agent loop. Approvals are tied to the session and a random operation id, are single use, expire, and run only the parameters stored at proposal time. Calendar conflicts are checked again just before running.
- **No deletion of email or files.** There are no delete or trash tools, and the bridge has no such operations. Event cancellation needs approval and an extra acknowledgement.
- **Secrets** live only in `.env` (git-ignored) and Apps Script properties. They are never returned by the API or logged. Logs record tool and operation names, never email content.
- **Prompt injection:** the model is told to treat email, event and file content as data. Even if it's tricked, a write still needs your approval and shows you exactly what will happen.
- **Bridge:** anyone with the URL can reach it, but every request needs the shared secret, and all inputs are validated again. Drive queries are built from structured, escaped filters, never raw query text.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Status bar: *Backend offline* | Start the backend (`python run.py` in `backend/`) and check port 8000 is free. |
| *Cannot connect to Ollama* | Start Ollama (`ollama serve`, or open the Ollama app) and check `OLLAMA_BASE_URL`. |
| *Ollama model '…' is not installed* | Run `ollama pull <model>` with the exact name in `OLLAMA_MODEL`. |
| *does not support tool calling* | Choose a tool-capable model (`qwen3`, `llama3.1`, `llama3.2`, `mistral-nemo`). |
| *The local model did not answer within 600 seconds* | Use a smaller model, lower `OLLAMA_NUM_CTX`, set `OLLAMA_THINK=false`, or raise `OLLAMA_TIMEOUT_SECONDS`. |
| The model gives poor answers or calls the wrong tool | Small local models are weaker at tool use. Try a larger model, be more specific, or use `LLM_PROVIDER=claude`. Writes still need your approval, so a wrong call can't do anything on its own. |
| *The model API is rate limiting requests* | Groq's free tier limits requests and tokens per minute and per day. Wait a minute, or ask for fewer emails at once. |
| *The model API rejected OPENAI_API_KEY* / *Model '…' was not found* | Check the key and `OPENAI_MODEL` against <https://console.groq.com/docs/models>, then restart the backend. |
| *Claude is not configured* | With `LLM_PROVIDER=claude`: set `ANTHROPIC_API_KEY` and `CLAUDE_MODEL`, then restart the backend. |
| *Claude model '…' was not found* | Fix `CLAUDE_MODEL`. |
| *Claude rejected the request … fallbacks* | Set `CLAUDE_REFUSAL_FALLBACK=false`. |
| *The Google bridge did not return JSON* | The Web App must be deployed with **Who has access: Anyone**, and the URL must end in `/exec`. |
| *Invalid shared secret* / *not_configured* | `APPS_SCRIPT_SHARED_SECRET` in `.env` and the `BRIDGE_SHARED_SECRET` script property must match exactly (no spaces or quotes). Run `checkSetup` to confirm it's set. |
| *Google did not answer within 30 seconds* | Apps Script was slow. Try again, or raise `BRIDGE_TIMEOUT_SECONDS`. For a failed **write**, check Gmail/Calendar/Drive before retrying, because it may have gone through. |
| *Google reported an error: … permission* | Re-authorize: run any function in the Apps Script editor. After changing scopes, deploy a new version. |
| *This session no longer exists* | The backend restarted. Send your request again. |
| *Another request for this session is still running* | Wait for the current reply to finish. |
| Times look wrong | Set `USER_TIMEZONE` to your IANA timezone. |
| `Invalid host header` | Open the app as `http://localhost:5173`, not by IP or another hostname. |
