#!/usr/bin/env python3
"""PostToolUse hook: Check relative links in Markdown files.

Detects broken relative links after Write/Edit operations on .md files.
Links annotated with <!-- pending: #N --> are suppressed from warnings.
Reports unannotated broken links as warnings to prompt issue tracking.

Adapted from infrastructure repo's postwrite-check-md-links.py for
ccmemo plugin use.
"""

import json
import os
import re
import sys

PENDING_ANNOTATION = re.compile(r"<!--\s*pending:\s*#(\d+)\s*-->")


def extract_relative_links(content: str) -> list[tuple[int, str, str, bool, str]]:
    """Extract relative links from Markdown content.

    Returns list of (lineno, text, target, is_annotated, issue_ref).
    """
    lines = content.splitlines()
    links = []
    in_code_block = False

    for lineno_idx, line in enumerate(lines):
        lineno = lineno_idx + 1

        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", line):
            text, target = match.group(1), match.group(2)

            # Skip external URLs and anchors
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("#"):
                continue

            # Strip anchor from path
            path = target.split("#")[0]
            if not path:
                continue

            # Check for pending annotation on same or previous line
            is_annotated = False
            issue_ref = ""
            annotation = PENDING_ANNOTATION.search(line)
            if not annotation and lineno_idx > 0:
                annotation = PENDING_ANNOTATION.search(lines[lineno_idx - 1])
            if annotation:
                is_annotated = True
                issue_ref = f"#{annotation.group(1)}"

            links.append((lineno, text, path, is_annotated, issue_ref))

    return links


def check_links(
    file_path: str, content: str
) -> tuple[list[str], list[str]]:
    """Check relative links in a file. Returns (broken, annotated)."""
    file_dir = os.path.dirname(os.path.abspath(file_path))
    broken = []
    annotated = []

    # Detect if this is a knowledge entry to resolve entries/-relative paths
    entries_dir = None
    abs_path = os.path.abspath(file_path)
    entries_marker = os.sep + ".claude" + os.sep + "knowledge" + os.sep + "entries" + os.sep
    if entries_marker in abs_path:
        # Find the entries/ root
        idx = abs_path.index(entries_marker) + len(entries_marker) - 1
        entries_dir = abs_path[:idx]

    for lineno, text, target, is_annotated, issue_ref in extract_relative_links(content):
        resolved = os.path.normpath(os.path.join(file_dir, target))

        # For knowledge entries, also try resolving from entries/ root
        exists = os.path.exists(resolved)
        if not exists and entries_dir:
            from_entries = os.path.normpath(os.path.join(entries_dir, target))
            exists = os.path.exists(from_entries)

        if not exists:
            if is_annotated:
                annotated.append(
                    f"L{lineno}: [{text}]({target}) (tracked by {issue_ref})"
                )
            else:
                broken.append(f"L{lineno}: [{text}]({target})")

    return broken, annotated


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        return

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".md"):
        return

    if not os.path.exists(file_path):
        return

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return

    broken, annotated = check_links(file_path, content)

    if not broken:
        return

    broken_list = "\n".join(f"  - {b}" for b in broken)
    parts = [
        f"[ccmemo] リンク切れ検出 ({os.path.basename(file_path)}):\n"
        f"{broken_list}\n"
        f"<!-- pending: #N --> アノテーションで追跡済みにするか、"
        f"リンク先を作成してください。",
    ]
    if annotated:
        annotated_list = "\n".join(f"  - {a}" for a in annotated)
        parts.append(f"\n追跡済み（警告抑制）:\n{annotated_list}")

    result = {
        "decision": "warn",
        "reason": "\n".join(parts),
    }
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
