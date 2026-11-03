# Requirements checklist

**The SRS v1.0 was not found** in the workspace or the home folder when this project was
built (searched by file name and content on 2026-11-03). This checklist was built
from the project brief instead. Every value marked **assumed** was chosen as a safe
default and must be checked against the SRS. All of them are environment variables, so
changing one only needs `.env`, not code.

Status key: ✅ implemented and covered by automated tests · 🟡 implemented, needs real
Google/Claude credentials to verify end to end.

## Numeric limits and rules

| Requirement | Value used | Source | Setting |
|---|---|---|---|
| Max agent steps (Claude calls per prompt) | 10 | assumed | `MAX_AGENT_STEPS` |
| Bridge timeout | 30 s | assumed | `BRIDGE_TIMEOUT_SECONDS` |
| Email body returned to Claude | 10,000 chars (truncated, flagged) | assumed | `EMAIL_BODY_MAX_CHARS` |
| Outgoing email / reply body | 20,000 chars | assumed | `OUTGOING_BODY_MAX_CHARS` |
| Subject length | 250 chars | assumed | `SUBJECT_MAX_CHARS` |
| Recipients per email (to+cc+bcc) | 20 | assumed | `MAX_RECIPIENTS` |
| Email search results | 20 (hard cap 50) | assumed | `EMAIL_SEARCH_MAX_RESULTS` |
| Tool result size sent to Claude | 20,000 chars | assumed | `TOOL_RESULT_MAX_CHARS` |
| Prompt length | 4,000 chars | assumed | `PROMPT_MAX_CHARS` |
| Working hours | 09:00–17:00, Mon–Fri | assumed | `WORK_DAY_START`, `WORK_DAY_END`, `WORK_DAYS` |
| Time-slot grid | 30 min | assumed | `SLOT_GRID_MINUTES` |
| Max suggested slots | 5 | assumed | `MAX_SUGGESTED_SLOTS` |
| Availability search range | 14 days | assumed | `AVAILABILITY_MAX_RANGE_DAYS` |
| Timezone | `UTC` until set | assumed | `USER_TIMEZONE` |
| Drive attachment folder | `Email Attachments` (in My Drive root) | assumed | `ATTACHMENT_FOLDER_NAME` |
| Max attachment size | 10 MB (bridge hard cap 25 MB) | assumed | `ATTACHMENT_MAX_BYTES` |
| Supported attachment types | pdf, doc(x), xls(x), ppt(x), csv, txt, png, jpg, jpeg, gif | assumed | `SUPPORTED_ATTACHMENT_EXTENSIONS` |
| Approval expiry | 10 min | assumed | `APPROVAL_TTL_SECONDS` |
| Session idle expiry | 60 min | assumed | `SESSION_IDLE_TTL_SECONDS` |
| Language model provider | Ollama (local) by default; Claude optional | user decision (replaces "Claude API" in the brief) | `LLM_PROVIDER` |
| Model name | from config only, no default | brief | `OLLAMA_MODEL` / `CLAUDE_MODEL` |
| Ollama context window | 16,384 tokens | assumed | `OLLAMA_NUM_CTX` |

## Functional requirements

| Area | Requirement | Status | Where |
|---|---|---|---|
| API | `GET /api/status`, `POST /api/prompt`, `POST /api/confirm`, `POST /api/reset` | ✅ | `backend/app/api/routes.py` |
| API | Request validation, readable JSON errors `{"error": {code, message}}` | ✅ | `main.py`, `schemas.py` |
| Agent | Tool use via Ollama (default) or Claude, agent loop, step limit, tool-result handling | ✅ | `agent/agent_loop.py`, `agent/ollama_client.py`, `agent/claude_client.py` |
| Agent | Invalid tool names/arguments and malformed results go back to Claude as errors | ✅ | `agent/agent_loop.py` |
| Agent | Model errors (Ollama down, model not pulled, no tool support, timeouts, Claude API errors) mapped to readable messages; history rolled back | ✅ | `agent/ollama_client.py`, `agent/claude_client.py` |
| Agent | Model is configurable; missing/unknown model reported clearly | ✅ | `config.py`, `claude_client.py` |
| Gmail | Search, read, list labels | ✅ 🟡 | `services/gmail_service.py`, `apps_script/Gmail.gs` |
| Gmail | Send, draft, reply, reply draft, forward, label/read changes (approval) | ✅ 🟡 | same |
| Gmail | Summarise / draft text (Claude writes it after `read_email`) | ✅ 🟡 | system prompt |
| Calendar | List, get, find free slots from real events | ✅ 🟡 | `services/calendar_service.py`, `availability.py`, `Calendar.gs` |
| Calendar | Create, update/move, cancel (approval) | ✅ 🟡 | same |
| Calendar | Working hours, slot grid, max suggestions, duration kept on move | ✅ | `availability.py`, `calendar_service.py` |
| Calendar | Conflicts detected, shown, need explicit acknowledgement; re-checked at execution | ✅ | `calendar_service.py`, `approvals.py` |
| Calendar | Timezone handling (naive times read in `USER_TIMEZONE`) | ✅ | `common.localize` |
| Calendar | Recurring events are not changed (refused with explanation) | ✅ | `calendar_service.py`, `Calendar.gs` |
| Drive | Search (structured filters), list, file info | ✅ 🟡 | `services/drive_service.py`, `Drive.gs` |
| Drive | Create folder, move file, save email attachments (approval) | ✅ 🟡 | same |
| Drive | Oversized / unsupported / duplicate attachments skipped with reasons | ✅ | same |
| Approval | Every write needs approval, bound to session + operation id, single use, expiring | ✅ | `approvals.py` |
| Approval | Frontend cannot change what runs (stored parameters only) | ✅ | `approvals.py`, `schemas.ConfirmRequest` |
| Approval | No permanent deletion of email or files; no delete/trash tools | ✅ | tool registry, bridge |
| Session | In-memory context per session; reset; nothing persists after restart | ✅ | `sessions.py` |
| Frontend | Chat, loading, errors, status, approval panel, approve/reject, results | ✅ | `frontend/src` |
| Security | Localhost only (bind, Host check, loopback client check, CORS) | ✅ | `main.py`, `run.py` |
| Security | Secrets only in `.env`; not sent to the frontend; not logged | ✅ | `config.py` (SecretStr) |
| Security | Email/file content treated as data, not instructions | ✅ (prompt rule) | `agent/prompts.py` |

## Decisions made without the SRS (please confirm)

1. **Saving a draft counts as a write** and needs approval (safe default).
2. **Cancelling an event** is allowed with approval and always shows a warning that needs acknowledgement. Email and file deletion are not offered at all.
3. **All-day events don't block availability** (they're usually holidays or reminders), but they're listed in the availability result. Events the user declined don't block time.
4. **Recurring events** are read-only through the agent.
5. **Labels**: only existing user labels can be added or removed. The agent can't create labels.
6. **Working hours are a warning, not a block**, for events the user asks for explicitly. Suggested slots always fall inside working hours.
7. One session can hold up to 5 operations waiting for approval at a time.
