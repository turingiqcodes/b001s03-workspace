#!/usr/bin/env python3
"""PreToolUse hook: stop secrets from being written to files or posted to GitHub.

Runs on Write/Edit and on the github MCP tools that publish text (PR bodies,
reviews, comments). Only high-confidence patterns are checked so the hook does
not fire on ordinary code. The matched value is never echoed back in full.
"""
import json
import re
import sys

PATTERNS = {
    "AWS access key id": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "AWS secret access key": re.compile(
        r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}\b", re.I),
    "private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "Anthropic/OpenAI API key": re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}\b"),
    "database URL with password": re.compile(
        r"\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis)://[^:\s/]+:[^@\s/$]{4,}@", re.I),
}
# Files that legitimately hold placeholders or that this hook's own patterns live in.
ALLOWED_PATHS = (".env.example", "scan_secrets.py")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def main():
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})
    path = tool_input.get("file_path", "")
    if path.endswith(ALLOWED_PATHS):
        return
    # For Edit, old_string is text already on disk; only new text matters.
    payload = {k: v for k, v in tool_input.items() if k not in ("old_string", "file_path")}
    for text in strings(payload):
        for name, pattern in PATTERNS.items():
            m = pattern.search(text)
            if m:
                shown = m.group(0)[:8] + "…"
                print(f"Blocked by secret scan: the {data.get('tool_name')} input contains what looks "
                      f"like a {name} ({shown}). Remove it, reference it from an Airflow Connection, "
                      "env var or secret store instead, and retry. If this is a known false positive, "
                      "ask the user to approve it explicitly.", file=sys.stderr)
                sys.exit(2)


if __name__ == "__main__":
    main()
