#!/usr/bin/env python3
"""PreToolUse hook: keep merges and pushes to protected branches under human control.

- Force-pushing to a protected branch is denied outright.
- Merging a PR (gh or the github MCP tool) and plain pushes to a protected
  branch require the user to confirm, even in auto/accept modes.
"""
import json
import re
import shlex
import sys

PROTECTED = {"main", "master", "develop", "production", "release"}


def respond(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def pushes_to_protected(words):
    # Refspecs look like "main", "HEAD:main", "+feature:refs/heads/main".
    for w in words:
        dest = w.split(":")[-1].removeprefix("refs/heads/").lstrip("+")
        if dest in PROTECTED:
            return dest
    return None


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool == "mcp__github__merge_pull_request":
        if tool_input.get("dry_run"):
            return
        respond("ask", f"Merging PR #{tool_input.get('pull_number', '?')} needs your confirmation.")

    if tool != "Bash":
        return
    cmd = tool_input.get("command", "")
    for part in re.split(r"&&|\|\||;|\n", cmd):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if words[:3] == ["gh", "pr", "merge"]:
            respond("ask", "gh pr merge needs your confirmation.")
        if words[:2] == ["git", "push"]:
            force = any(w in ("-f", "--force", "--force-with-lease", "--mirror")
                        or w.startswith("--force") for w in words) or any(
                w.startswith("+") for w in words[2:])
            branch = pushes_to_protected(words[2:])
            if force and branch:
                respond("deny", f"Force-pushing to protected branch '{branch}' is not allowed.")
            if branch:
                respond("ask", f"Pushing directly to protected branch '{branch}' needs your confirmation.")


if __name__ == "__main__":
    main()
