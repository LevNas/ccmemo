#!/usr/bin/env python3
"""Self-tests for hooks/postwrite_kb_lint.py — lint an entry right after a write.

Run: python3 tests/test_postwrite_kb_lint.py   (exit 0 = all pass)
Pure stdlib; drives the hook as a subprocess with a synthetic PostToolUse payload.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "postwrite_kb_lint.py")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        FAILURES.append(name)


def run_hook(tool_name, file_path):
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout


# Schema-3 identity + trust lines for fixtures that must lint clean.
S3 = ("id: 6f1c2a4e-0d3b-4c5e-9a7f-1b2c3d4e5f60\n"
      "generated:\n  by: human:alice\n  at: 2026-07-04T10:00:00+09:00\n")


def make_repo(base, schema=None):
    root = os.path.join(base, "repo", ".claude", "knowledge", "entries", "2026", "07")
    os.makedirs(root)
    with open(os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md"), "w", encoding="utf-8") as f:
        if schema:
            f.write(f"---\nschema_version: {schema}\n---\n")
        f.write("- #pitfall — traps (1)\n")
    return root


def test_findings_reported_as_warning():
    with tempfile.TemporaryDirectory() as base:
        root = make_repo(base, schema=2)
        path = os.path.join(root, "20260704-100000-alice-bad.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write('---\ntitle: Bad\ntags: "#pitfall"\n' + S3 + '---\n\n- see: [x](2026/07/nope.md)\n')
        code, out = run_hook("Write", path)
        check("exit 0", code == 0, code)
        data = json.loads(out)
        check("decision warn", data.get("decision") == "warn", out)
        reason = data.get("reason", "")
        check("missing-description reported", "missing-description" in reason, reason)
        check("unlabeled-link reported", "unlabeled-link" in reason, reason)
        check("broken-link reported", "broken-link" in reason, reason)
        check("no advisory tail at schema 2", "advisory" not in reason, reason)


def test_undeclared_corpus_gets_one_line_advisory():
    with tempfile.TemporaryDirectory() as base:
        root = make_repo(base)  # no schema_version declaration
        path = os.path.join(root, "20260704-100000-alice-old.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write('---\ntitle: Old\ntags: "#pitfall"\n---\n\nOld entry, no description.\n')
        code, out = run_hook("Edit", path)
        data = json.loads(out)
        reason = data.get("reason", "")
        check("advisory-only: warn with one line", data.get("decision") == "warn"
              and reason.count("\n") == 0 and "3 advisory" in reason, reason)
        check("advisory-only: names the declaration", "schema_version" in reason, reason)
        # an enforced finding plus advisory ones: list + count tail
        with open(path, "a", encoding="utf-8") as f:
            f.write("- see: [x](2026/07/nope.md)\n")
        code, out = run_hook("Edit", path)
        reason = json.loads(out).get("reason", "")
        check("mixed: enforced listed", "broken-link" in reason, reason)
        check("mixed: advisory counted, not listed",
              "+4 advisory" in reason and "missing-description:" not in reason, reason)


def test_clean_entry_is_silent():
    with tempfile.TemporaryDirectory() as base:
        root = make_repo(base)
        path = os.path.join(root, "20260704-100000-alice-good.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write('---\ntitle: Good\ntags: "#pitfall"\n' + S3 +
                    'description: "Open when the post-write lint hook needs a clean fixture '
                    'whose trigger condition is long enough."\n---\n\nFine.\n')
        code, out = run_hook("Edit", path)
        check("clean: exit 0 and no output", code == 0 and out.strip() == "", out)


def test_non_entry_and_non_write_ignored():
    with tempfile.TemporaryDirectory() as base:
        root = make_repo(base)
        other = os.path.join(base, "repo", "README.md")
        with open(other, "w", encoding="utf-8") as f:
            f.write("# not an entry\n")
        code, out = run_hook("Write", other)
        check("non-entry ignored", code == 0 and out.strip() == "", out)
        path = os.path.join(root, "20260704-100000-alice-bad.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\ntitle: Bad\n---\n\nx\n")
        code, out = run_hook("Read", path)
        check("non-write tool ignored", code == 0 and out.strip() == "", out)
        code, out = run_hook("Write", os.path.join(root, "missing.md"))
        check("missing file ignored", code == 0 and out.strip() == "", out)


if __name__ == "__main__":
    test_findings_reported_as_warning()
    test_undeclared_corpus_gets_one_line_advisory()
    test_clean_entry_is_silent()
    test_non_entry_and_non_write_ignored()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
