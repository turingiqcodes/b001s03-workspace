"""Logging and redaction (NFR-009, NFR-025)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from oncall_agent.logging_config import (
    REDACTED,
    JsonFormatter,
    redact,
    redact_value,
    register_secrets,
    setup_logging,
)

pytestmark = pytest.mark.unit


def _record(msg: str, *args, exc_info=None, **extra) -> logging.LogRecord:
    record = logging.LogRecord("oncall_agent.test", logging.INFO, __file__, 1, msg, args, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_registered_secret_values_are_redacted():
    register_secrets(["my-very-secret-value"])
    assert redact("token is my-very-secret-value!") == f"token is {REDACTED}!"


def test_short_values_are_not_registered():
    register_secrets(["abc"])
    assert redact("abc def") == "abc def"


@pytest.mark.parametrize(
    "text,leak",
    [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("key sk-ant-api03-abcdefghijklmnop", "sk-ant-api03-abcdefghijklmnop"),
        ("groq gsk_AbCdEfGhIjKlMnOpQrSt1234", "gsk_AbCdEfGhIjKlMnOpQrSt1234"),
        ("linear lin_api_ABCDEFGHIJKLMNOP1234", "lin_api_ABCDEFGHIJKLMNOP1234"),
        ("github ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("github github_pat_11ABCDEFG0123456789_abcdefghijk", "github_pat_11ABCDEFG0123456789_abcdefghijk"),
        ('{"password": "hunter2hunter2"}', "hunter2hunter2"),
        ("https://x/?api_key=abc123def456&x=1", "abc123def456"),
        ('{"Authorization": "lin_api_zzzzzzzzzzzz"}', "lin_api_zzzzzzzzzzzz"),
    ],
)
def test_credential_patterns_are_redacted(text, leak):
    out = redact(text)
    assert leak not in out
    assert REDACTED in out


def test_ordinary_text_is_unchanged():
    text = "Task extract failed: max_tokens=4096 token_count 12 ConnectionError: timed out"
    assert redact(text) == text


def test_redact_value_handles_nested_structures():
    register_secrets(["nested-secret-123"])
    data = {"a": ["x nested-secret-123"], "b": {"c": "Bearer abcdefghij"}, "n": 5}
    out = redact_value(data)
    assert "nested-secret-123" not in json.dumps(out)
    assert "abcdefghij" not in json.dumps(out)
    assert out["n"] == 5


def test_json_formatter_emits_valid_json_with_extras():
    line = JsonFormatter().format(_record("polled %d runs", 3, dag_id="etl", incident_id=7))
    payload = json.loads(line)
    assert payload["message"] == "polled 3 runs"
    assert payload["level"] == "INFO"
    assert payload["dag_id"] == "etl" and payload["incident_id"] == 7
    assert payload["ts"].endswith("+00:00")


def test_json_formatter_redacts_exception_text():
    register_secrets(["exception-secret-xyz"])
    try:
        raise RuntimeError("failed with exception-secret-xyz")
    except RuntimeError:
        import sys

        line = JsonFormatter().format(_record("boom", exc_info=sys.exc_info()))
    assert "exception-secret-xyz" not in line
    assert "RuntimeError" in json.loads(line)["exception"]


def test_setup_logging_is_idempotent_and_writes_json_file(tmp_path: Path):
    setup_logging("INFO", "text", tmp_path, secrets=["file-secret-value-1"])
    setup_logging("INFO", "text", tmp_path, secrets=["file-secret-value-1"])
    logger = logging.getLogger("oncall_agent")
    ours = [h for h in logger.handlers if getattr(h, "_oncall_handler", False)]
    assert len(ours) == 2  # stderr + file, not duplicated (pytest adds its own capture handlers)
    logging.getLogger("oncall_agent.x").info("using file-secret-value-1", extra={"dag_id": "d"})
    for h in logger.handlers:
        h.flush()
    lines = (tmp_path / "agent.log").read_text().strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["message"] == f"using {REDACTED}"
    assert payload["dag_id"] == "d"


def test_stderr_text_output_is_redacted(capsys):
    setup_logging("INFO", "text", None, secrets=["stderr-secret-value"])
    logging.getLogger("oncall_agent.y").warning("value=%s", "stderr-secret-value")
    err = capsys.readouterr().err
    assert "stderr-secret-value" not in err and REDACTED in err
