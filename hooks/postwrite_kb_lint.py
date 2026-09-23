#!/usr/bin/env python3
"""PostToolUse hook: lint a knowledge entry right after it is written.

Runs ``scripts/kb_graph.py lint <file>`` for every Write/Edit that touches a
file under ``.claude/knowledge/entries/`` and reports the findings for that
file as a warning (the harness cannot undo a write, so this is advisory: the
model sees the findings in the same turn and fixes them while the entry is
still in context — instead of a reviewer finding a missing description or an
unlabeled link weeks later). The checks and their meaning are documented in
docs/link-graph.md; the conventions they enforce are owned by ccmemo, so a
harness plugin or a merge gate that wants the same checks calls this CLI
rather than re-implementing them.

Silent no-op when the file is not a knowledge entry, when the graph CLI is
missing, or when the lint is clean. Never blocks. Pure stdlib.
"""

import json
import os
import subprocess
import sys

ENTRIES_MARKER = os.sep + ".claude" + os.sep + "knowledge" + os.sep + "entries" + os.sep
KB_GRAPH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "kb_graph.py")
MAX_FINDINGS_SHOWN = 12


def entries_root(file_path: str) -> str:
    """The `.../.claude/knowledge/entries` dir containing file_path, or ''."""
    abs_path = os.path.abspath(file_path)
    idx = abs_path.find(ENTRIES_MARKER)
    if idx == -1:
        return ""
    return abs_path[: idx + len(ENTRIES_MARKER) - 1]


def lint_file(file_path: str) -> list[dict]:
    root = entries_root(file_path)
    if not root or not os.path.isfile(KB_GRAPH):
        return []
    if os.path.basename(file_path) == "CLAUDE.md":
        return []
    try:
        proc = subprocess.run(
            [sys.executable, KB_GRAPH, "--root", root, "--json", "lint", file_path],
            capture_output=True, text=True, timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if not proc.stdout.strip():
        return []
    try:
        findings = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return findings if isinstance(findings, list) else []


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return
    if input_data.get("tool_name", "") not in ("Write", "Edit"):
        return
    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".md") or not os.path.exists(file_path):
        return

    findings = lint_file(file_path)
    if not findings:
        return

    lines = [f"  - {f['check']}: {f['detail']}" for f in findings[:MAX_FINDINGS_SHOWN]]
    if len(findings) > MAX_FINDINGS_SHOWN:
        lines.append(f"  - … {len(findings) - MAX_FINDINGS_SHOWN} more")
    reason = (
        f"[ccmemo] kb_graph lint ({os.path.basename(file_path)}): {len(findings)} finding(s)\n"
        + "\n".join(lines)
        + "\n今のうちに修正してください（description はトリガー条件、リンクには「— なぜ辿るか」のラベル、"
        "amends/extends は相手側からの逆リンク）。検査の一覧: docs/link-graph.md の lint 節。"
    )
    json.dump({"decision": "warn", "reason": reason}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
