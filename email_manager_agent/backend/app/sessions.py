"""In-memory session store.

Sessions hold the Claude conversation and pending operations. They live only in this
process: restarting the server clears them (nothing is written to disk).
"""
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.errors import AppError, NotFound, ValidationFailed

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_SESSIONS = 20  # single-user app: a handful of browser tabs at most


class SessionBusy(AppError):
    code = "session_busy"
    http_status = 409


@dataclass
class PendingOperation:
    id: str
    tool_name: str
    summary: str
    details: dict[str, Any]
    warnings: list[str]
    conflict_event_ids: list[str]
    execute_params: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    status: str = "pending"  # pending | executing | executed | rejected | expired | failed | cancelled
    result: Any = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        """What the frontend may see. execute_params stay on the server."""
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "summary": self.summary,
            "details": self.details,
            "warnings": self.warnings,
            "requires_acknowledgement": bool(self.warnings),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }


@dataclass
class Session:
    id: str
    created_at: datetime
    last_active: datetime
    messages: list[dict[str, Any]] = field(default_factory=list)
    # tool_result blocks that must open the next user message (set when the step limit cut a turn short)
    carryover_blocks: list[dict[str, Any]] = field(default_factory=list)
    # approval outcomes Claude has not been told about yet
    pending_notes: list[str] = field(default_factory=list)
    operations: dict[str, PendingOperation] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def open_operations(self) -> list[PendingOperation]:
        return [op for op in self.operations.values() if op.status == "pending"]

    def expire_operations(self, now: datetime) -> None:
        for op in self.open_operations():
            if now >= op.expires_at:
                op.status = "expired"
                self.pending_notes.append(
                    f"Operation {op.id} ({op.tool_name}: {op.summary}) expired without approval. It was not done."
                )


class SessionStore:
    def __init__(self, idle_ttl_seconds: int, now: Callable[[], datetime]):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._idle_ttl = idle_ttl_seconds
        self._now = now

    def get_or_create(self, session_id: str | None) -> Session:
        """Return the session for session_id, or a new one if it is missing or unknown."""
        if session_id is not None and not _SESSION_ID_PATTERN.match(session_id):
            raise ValidationFailed("session_id has an invalid format.")
        now = self._now()
        with self._lock:
            self._drop_idle(now)
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    oldest = min(self._sessions.values(), key=lambda s: s.last_active)
                    del self._sessions[oldest.id]
                session = Session(id=secrets.token_urlsafe(24), created_at=now, last_active=now)
                self._sessions[session.id] = session
            session.last_active = now
            return session

    def get(self, session_id: str) -> Session:
        """Return an existing session or raise NotFound (used by /confirm, which must not create one)."""
        if not _SESSION_ID_PATTERN.match(session_id or ""):
            raise ValidationFailed("session_id has an invalid format.")
        with self._lock:
            self._drop_idle(self._now())
            session = self._sessions.get(session_id)
            if session is None:
                raise NotFound("This session no longer exists (the server may have restarted). Start a new request.")
            session.last_active = self._now()
            return session

    def reset(self, session_id: str | None) -> Session:
        """Forget a session completely (history and pending operations) and start a fresh one."""
        with self._lock:
            if session_id:
                self._sessions.pop(session_id, None)
        return self.get_or_create(None)

    def peek(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def _drop_idle(self, now: datetime) -> None:
        for sid in [s.id for s in self._sessions.values() if (now - s.last_active).total_seconds() > self._idle_ttl]:
            del self._sessions[sid]


class SessionGuard:
    """Hold a session's lock for one request; refuse (instead of queueing) if it is busy."""

    def __init__(self, session: Session):
        self._session = session

    def __enter__(self) -> Session:
        if not self._session.lock.acquire(blocking=False):
            raise SessionBusy("Another request for this session is still running. Please wait for it to finish.")
        return self._session

    def __exit__(self, *exc_info: object) -> None:
        self._session.lock.release()
