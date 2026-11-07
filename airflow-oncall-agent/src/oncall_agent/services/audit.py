"""Audit trail (SRS §7.5, FR-053/064/072, NFR-025..NFR-029).

Each event is written twice:

* to the ``audit_events`` SQLite table (queried by ``oncall-agent audit <id>``), and
* as one JSON object per line to ``<log_dir>/audit.jsonl`` for log shipping.

All text is redacted before it is stored (NFR-009) and long summaries are truncated.
Recording an audit event never raises: an audit failure is logged as an error, but it
must not abort the operation being audited or stop monitoring (NFR-004).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..logging_config import redact, redact_value
from ..models import AuditEvent, AuditEventType
from ..store import Store

log = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 2000


def _summary(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    text = redact(text)
    suffix = "…[truncated]"
    return text if len(text) <= MAX_SUMMARY_CHARS else text[: MAX_SUMMARY_CHARS - len(suffix)] + suffix


class AuditService:
    def __init__(self, store: Store, jsonl_path: Path | None = None) -> None:
        self.store = store
        self.jsonl_path = jsonl_path
        self._file_lock = threading.Lock()
        if jsonl_path is not None:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event_type: AuditEventType,
        *,
        actor: str,
        action: str,
        incident_id: int | None = None,
        input_summary: Any = None,
        output_summary: Any = None,
        success: bool = True,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent | None:
        """Record an event. Returns the stored event, or None if it could not be stored."""
        try:
            event = AuditEvent(
                incident_id=incident_id,
                event_type=event_type,
                actor=actor,
                action=action,
                input_summary=_summary(input_summary),
                output_summary=_summary(output_summary),
                success=success,
                details=redact_value(details or {}),
            )
        except Exception:  # invalid arguments are a bug, but must not break the caller
            log.exception("Invalid audit event %s/%s", event_type, action)
            return None

        stored: AuditEvent | None = None
        try:
            stored = event.model_copy(update={"id": self.store.insert_audit_event(event)})
        except Exception:
            log.error("Could not store audit event %s for incident %s", event_type.value, incident_id, exc_info=True)

        self._append_jsonl(stored or event, persisted=stored is not None)
        return stored

    def history(self, incident_id: int) -> list[AuditEvent]:
        return self.store.list_audit_events(incident_id)

    def _append_jsonl(self, event: AuditEvent, *, persisted: bool) -> None:
        if self.jsonl_path is None:
            return
        line = event.model_dump(mode="json")
        line["persisted"] = persisted
        try:
            with self._file_lock, self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            log.error("Could not write audit log %s", self.jsonl_path, exc_info=True)
