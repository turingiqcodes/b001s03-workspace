"""PostToolUse hook: after Claude edits an app file, ask it to apply the app-code-quality skill.

Runs ruff on edited Python files (the skill's Tooling step) and hands the result back to Claude
as additionalContext, along with an instruction to check the change against the skill's checklist.
"""

import json
import os
import subprocess
import sys

APP_DIRS = ("backend", "frontend", "tests")
CODE_EXTS = (".py", ".js", ".html", ".css")

event = json.load(sys.stdin)
path = event.get("tool_input", {}).get("file_path", "")
project = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
rel = os.path.relpath(path, project) if path else ""

# Only app code: skip .claude/, README, config files and anything outside the project.
if not rel.startswith(APP_DIRS) or not rel.endswith(CODE_EXTS):
    sys.exit(0)

lint = ""
if rel.endswith(".py"):
    ruff = os.path.join(project, ".venv", "bin", "ruff")
    result = subprocess.run([ruff, "check", path], capture_output=True, text=True, cwd=project)
    lint = result.stdout.strip() or "ruff check: no issues."

context = (
    f"{rel} was just changed. Apply the app-code-quality skill "
    "(.claude/skills/app-code-quality/SKILL.md) to this change only: duplication that can drift, "
    "error-handling consistency, naming, dead code. Fix small issues in place; mention larger ones briefly."
)
if lint:
    context += f"\n\n{lint}"

print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
