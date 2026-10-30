#!/usr/bin/env python3
"""PostToolUse hook: report a syntax error right after a .py file is written or edited.

Compiles in memory (no __pycache__ is written). Exit code 2 feeds the error back
to Claude so it fixes the file before moving on, e.g. while applying review fixes.
"""
import json
import sys


def main():
    data = json.load(sys.stdin)
    path = data.get("tool_input", {}).get("file_path", "")
    if not path.endswith(".py"):
        return
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return
    try:
        compile(source, path, "exec")
    except SyntaxError as exc:
        print(f"{path}:{exc.lineno}:{exc.offset}: SyntaxError: {exc.msg}. Fix it before continuing.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
