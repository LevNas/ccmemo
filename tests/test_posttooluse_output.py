#!/usr/bin/env python3
"""Self-tests for the output shape of the PostToolUse hooks.

PostToolUse accepts only "block" as a decision. Any other value is reported as
a hook error and its reason never reaches Claude, so a warning has to go out as
hookSpecificOutput.additionalContext.

Run: python3 tests/test_posttooluse_output.py   (exit 0 = all pass)
Pure stdlib; drives each hook as a subprocess with a synthetic payload.
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOOKS = os.path.join(ROOT, "hooks")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        FAILURES.append(name)


def run_hook(script, file_path):
    payload = {"tool_name": "Write", "tool_input": {"file_path": file_path}}
    proc = subprocess.run([sys.executable, os.path.join(HOOKS, script)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, timeout=30)
    return proc.returncode, proc.stdout


def context(out):
    """The additionalContext of a PostToolUse output; '' when the shape is wrong."""
    out = json.loads(out).get("hookSpecificOutput", {})
    return out.get("additionalContext", "") if out.get("hookEventName") == "PostToolUse" else ""


def test_md_links_reports_as_context():
    with tempfile.TemporaryDirectory() as base:
        path = os.path.join(base, "doc.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[gone](missing.md)\n")
        code, out = run_hook("postwrite_check_md_links.py", path)
        check("md links: exit 0", code == 0, code)
        check("md links: broken link in additionalContext",
              "missing.md" in context(out), out)


def test_redact_reports_as_context():
    with tempfile.TemporaryDirectory() as base:
        entries = os.path.join(base, ".claude", "knowledge", "entries")
        os.makedirs(entries)
        path = os.path.join(entries, "20261003-000000-alice-x.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("contact: alice@example.com\n")
        code, out = run_hook("postwrite_redact_entries.py", path)
        check("redact: exit 0", code == 0, code)
        check("redact: notice in additionalContext", "redact" in context(out), out)
        with open(path, encoding="utf-8") as f:
            check("redact: value masked in the file", "alice@example.com" not in f.read())


def test_no_hook_emits_an_unknown_decision():
    # "block" is the only decision value the hook events accept.
    pattern = re.compile(r"""["']decision["']\s*:\s*["'](\w+)["']""")
    for path in sorted(glob.glob(os.path.join(HOOKS, "*.py"))):
        with open(path, encoding="utf-8") as f:
            values = set(pattern.findall(f.read())) - {"block"}
        check(f"decision values: {os.path.basename(path)}", not values, values)


if __name__ == "__main__":
    test_md_links_reports_as_context()
    test_redact_reports_as_context()
    test_no_hook_emits_an_unknown_decision()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
