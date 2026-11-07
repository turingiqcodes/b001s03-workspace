"""Application logging: text or JSON output with secret redaction (NFR-009, NFR-025).

Redaction runs on the *final formatted string*, so it also covers exception tracebacks
and ``extra`` fields. Two layers are applied:

* exact values of every configured secret (registered at startup from Settings), and
* patterns that look like credentials even when they are not configured here
  (bearer tokens, Groq/Anthropic/Linear/GitHub key formats, ``password=...``).

The structured *audit* trail is written by ``services/audit.py`` to SQLite and a JSON
lines file; it uses :func:`redact` from this module before writing.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REDACTED = "***REDACTED***"

# Credential-shaped strings. Each pattern's first group (if any) is kept so the
# output still shows *what* was redacted, e.g. "Authorization: Bearer ***REDACTED***".
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?!bearer\s)[^\s\"',}]+"),
    re.compile(r"()gsk_[A-Za-z0-9]{8,}"),
    re.compile(r"()sk-ant-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"()lin_api_[A-Za-z0-9]{8,}"),
    re.compile(r"()(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"()github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)((?:password|passwd|api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}&]+"),
]

_lock = threading.Lock()
_secret_values: set[str] = set()

# Attributes every LogRecord has; anything else came from ``extra=`` and is emitted
# as a JSON field.
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def register_secrets(values: list[str]) -> None:
    """Register exact secret values to redact. Very short values are ignored to avoid
    mangling ordinary words (a 3-character "secret" is a configuration problem anyway)."""
    with _lock:
        _secret_values.update(v for v in values if v and len(v) >= 6)


def clear_registered_secrets() -> None:
    """Testing helper."""
    with _lock:
        _secret_values.clear()


def redact(text: str) -> str:
    """Return ``text`` with registered secrets and credential-shaped strings removed."""
    if not text:
        return text
    with _lock:
        secrets = sorted(_secret_values, key=len, reverse=True)  # longest first
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) or "") + REDACTED, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside dicts/lists (for structured payloads)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


class RedactingTextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, exception, plus extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


_HANDLER_TAG = "_oncall_handler"


def setup_logging(level: str = "INFO", fmt: str = "text", log_dir: Path | None = None, secrets: list[str] | None = None) -> None:
    """Configure the ``oncall_agent`` logger. Safe to call more than once.

    Logs go to stderr in the chosen format and, when ``log_dir`` is given, to a
    rotating ``agent.log`` file that is always JSON (easier for log shippers).
    """
    register_secrets(secrets or [])
    logger = logging.getLogger("oncall_agent")
    logger.setLevel(level.upper())
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(JsonFormatter() if fmt == "json" else RedactingTextFormatter())
    setattr(stream, _HANDLER_TAG, True)
    logger.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "agent.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        setattr(file_handler, _HANDLER_TAG, True)
        logger.addHandler(file_handler)

    # httpx logs every request URL at INFO; keep it quiet unless debugging.
    logging.getLogger("httpx").setLevel(logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
