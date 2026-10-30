#!/usr/bin/env python3
"""PreToolUse hook for the PR reviewer agents: allow only read-only shell commands.

Reviewers inspect code; they never change the working tree, the index or the
remote. Every command segment (split on ; && || | &) must start with an
allowlisted program, and output may only be redirected to /dev/null. Anything
else is denied with exit code 2 and a reason the agent can act on.
"""
import json
import shlex
import sys

READ_ONLY = {
    "cat", "head", "tail", "ls", "wc", "grep", "rg", "find", "diff", "file",
    "stat", "sort", "uniq", "cut", "tr", "nl", "basename", "dirname", "pwd",
    "echo", "true", "tree", "realpath", "cd", "sed", "git", "gh", "comm",
}
GIT_READ_ONLY = {
    "diff", "log", "show", "status", "blame", "ls-files", "ls-tree", "rev-parse",
    "merge-base", "branch", "grep", "shortlog", "cat-file", "describe", "name-rev",
    "rev-list", "range-diff",
}
GIT_BRANCH_FLAGS = {
    "-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "--contains",
    "--merged", "--no-merged", "--show-current",
}
GH_READ_ONLY = {("pr", "view"), ("pr", "diff"), ("pr", "checks"), ("pr", "list"), ("repo", "view")}
FIND_WRITERS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
SEPARATORS = {";", "&&", "||", "|", "&", ";;"}


def deny(reason):
    print(f"Blocked by PR-review read-only guard: {reason}. "
          "Reviewers must not modify files, git state or the remote; use the Read tool "
          "or a read-only command instead.", file=sys.stderr)
    sys.exit(2)


def check_segment(words):
    if not words:
        return
    prog = words[0].rsplit("/", 1)[-1]
    if prog not in READ_ONLY:
        deny(f"'{prog}' is not on the read-only allowlist")
    args = words[1:]
    if prog == "sed" and any(a.startswith("-i") or a == "--in-place" for a in args):
        deny("sed -i edits files in place")
    if prog == "find" and FIND_WRITERS.intersection(args):
        deny("find with -exec/-delete/-fprint can modify files")
    if prog == "git":
        # Skip global options such as --no-pager and -C <dir>.
        while args and args[0].startswith("-"):
            if args[0] == "-c":
                deny("git -c can override config to run arbitrary commands")
            args = args[2:] if args[0] == "-C" else args[1:]
        if not args or args[0] not in GIT_READ_ONLY:
            deny(f"'git {args[0] if args else ''}' is not a read-only git command")
        if args[0] == "branch" and any(a.startswith("-") and a not in GIT_BRANCH_FLAGS for a in args[1:]):
            deny("git branch may only be used to list branches")
        if args[0] == "branch" and not any(a.startswith("-") for a in args[1:]) and len(args) > 1:
            deny("'git branch <name>' creates a branch")
        if any(a == "--output" or a.startswith("--output=") for a in args):
            deny("git --output writes a file")
    if prog == "gh" and tuple(args[:2]) not in GH_READ_ONLY:
        deny(f"'gh {' '.join(args[:2])}' is not a read-only gh command")


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    cmd = data.get("tool_input", {}).get("command", "")
    if "`" in cmd or "$(" in cmd or "<(" in cmd or ">(" in cmd:
        deny("command substitution is not allowed")

    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        deny(f"could not parse command ({exc})")

    segment = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in SEPARATORS:
            check_segment(segment)
            segment = []
        elif tok in ("(", ")"):
            deny("subshells are not allowed")
        elif ">" in tok:
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            ok = target == "/dev/null" or (tok == ">&" and target in ("1", "2"))
            if not ok:
                deny("redirecting output to a file is not allowed")
            if segment and segment[-1] in ("1", "2"):
                segment.pop()  # the fd number in 2>/dev/null, not an argument
            i += 1
        elif tok == "<":
            i += 1  # input redirection only reads
        else:
            segment.append(tok)
        i += 1
    check_segment(segment)


if __name__ == "__main__":
    main()
