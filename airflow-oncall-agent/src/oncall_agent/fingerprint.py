"""Error normalization and fingerprinting (FR-013..FR-016, FR-019, FR-020; AT-009).

Fingerprint = SHA-256 of ``dag_id|task_id|normalized_error_line`` (FR-015).

1. **Pick the error line** (:func:`extract_error_line`) from the task log / stack trace:
   the *last* line that looks like a Python exception (``KeyError: 'x'``,
   ``psycopg2.errors.LockNotAvailable: ...``). If there is none, the last meaningful
   line (Airflow boilerplate such as "Task failed with exception" is skipped). If there
   is no text at all, the constant ``<no log available>``.
2. **Normalize it** (:func:`normalize_error`): strip the log prefix and replace volatile
   values (timestamps, dates, memory addresses, UUIDs, long hex/IDs, run IDs, temp
   paths, IP addresses, PIDs, durations, large numbers) with placeholders, so that two
   occurrences of the same problem hash identically.

What is deliberately NOT normalized: short numbers (HTTP status codes, return codes
like ``-9``, ``timeout=30``) and quoted identifiers (``'customer_tier'``) — they
distinguish genuinely different failures.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

NO_LOG = "<no log available>"
MAX_LINE_CHARS = 500
ALGORITHM = "sha256"

# "[2026-11-05T06:00:31+00:00] ERROR - msg", "2026-11-05 06:00:31,123 ERROR - msg",
# "ERROR - msg", "[2026-11-05, 06:00:31 UTC] {taskinstance.py:123} ERROR - msg"
_LOG_PREFIX = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)?"  # [timestamp]
    r"(?:\d{4}-\d{2}-\d{2}[ T][\d:.,]+\S*\s+)?"  # bare timestamp
    r"(?:\{[^}]*\}\s*)?"  # {file.py:123}
    r"(?:(?:DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s*-\s*)?",
    re.IGNORECASE,
)

# A Python exception line: optional dotted module path, a CapWords class name, a colon
# (or end of line). Class names must look like exceptions to avoid matching prose.
_EXCEPTION_LINE = re.compile(
    r"^(?:[A-Za-z_]\w*\.)*"
    r"(?:[A-Z]\w*(?:Error|Exception|Exit|Interrupt|Timeout|Failure|Warning|NotAvailable|Fault|Refused|Denied)"
    r"|(?:[a-z_]\w*\.)+[A-Z]\w*)"
    r"(?::\s.*|:$|$)"
)

# Airflow boilerplate that never identifies the cause.
_NOISE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^traceback \(most recent call last\):?$",
        r"^file \".*\", line \d+",
        r"^task failed with exception$",
        r"^marking task as (failed|up_for_retry)",
        r"^starting attempt \d+ of \d+",
        r"^\^+$",
        r"^-+$",
        r"^::(end)?group::",
        r"^during handling of the above exception",
        r"^the above exception was the direct cause",
    )
]

# (pattern, replacement), applied in order. Order matters: full timestamps before dates,
# UUIDs before generic hex.
_VOLATILE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:scheduled|manual|backfill|dataset_triggered|asset_triggered)__[^\s'\"]+"), "<run_id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2}|\s?UTC)?"), "<ts>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<addr>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"(?:/tmp|/var/tmp|/private/var/folders)/[^\s'\":,)]+"), "<tmp_path>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b(pid|process|thread|job_id|task_id_num)[=: ]+\d+\b", re.IGNORECASE), r"\1=<n>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|milliseconds|s|sec|secs|seconds|min|mins|minutes)\b"), "<duration>"),
    (re.compile(r"\b\d{1,3}(?:,\d{3})+\b"), "<n>"),  # 48,211,903
    (re.compile(r"(?<![\w.-])\d{5,}\b"), "<n>"),  # long IDs / counts, keeps codes like 503 and -9
]


@dataclass(frozen=True)
class Fingerprint:
    value: str
    dag_id: str
    task_id: str
    original_error_line: str
    normalized_error_line: str
    error_source: str  # "log" | "stack_trace" | "message" | "none"

    def inputs(self) -> dict[str, Any]:
        """What went into the hash, stored with the incident for audit (FR-020)."""
        return {
            "algorithm": ALGORITHM,
            "format": "dag_id|task_id|normalized_error_line",
            "dag_id": self.dag_id,
            "task_id": self.task_id,
            "normalized_error_line": self.normalized_error_line,
            "original_error_line": self.original_error_line,
            "error_source": self.error_source,
        }


def _strip_prefix(line: str) -> str:
    return _LOG_PREFIX.sub("", line, count=1).strip()


def _is_noise(line: str) -> bool:
    return any(p.search(line) for p in _NOISE)


def extract_error_line(text: str | None) -> str:
    """Choose the line that best identifies the failure (see module docstring)."""
    if not text or not text.strip():
        return NO_LOG
    lines = [_strip_prefix(raw) for raw in text.splitlines()]
    lines = [line for line in lines if line]
    for line in reversed(lines):
        if _EXCEPTION_LINE.match(line) and not _is_noise(line):
            return line[:MAX_LINE_CHARS * 4]
    for line in reversed(lines):
        if not _is_noise(line):
            return line[:MAX_LINE_CHARS * 4]
    return NO_LOG


def normalize_error(line: str) -> str:
    """Replace volatile values with placeholders and collapse whitespace (FR-013/014)."""
    text = _strip_prefix(line)
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_LINE_CHARS] if text else NO_LOG


def compute_fingerprint(dag_id: str, task_id: str, normalized_error_line: str) -> str:
    """SHA-256 of ``dag_id|task_id|normalized_error_line`` (FR-015)."""
    payload = f"{dag_id}|{task_id}|{normalized_error_line}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_error(dag_id: str, task_id: str, error_text: str | None, *, error_source: str) -> Fingerprint:
    """Extract, normalize and hash in one step."""
    original = extract_error_line(error_text)
    normalized = normalize_error(original) if original != NO_LOG else NO_LOG
    return Fingerprint(
        value=compute_fingerprint(dag_id, task_id, normalized),
        dag_id=dag_id,
        task_id=task_id,
        original_error_line=original,
        normalized_error_line=normalized,
        error_source=error_source if original != NO_LOG else "none",
    )
