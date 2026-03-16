#!/usr/bin/env python3
"""PostToolUse hook: auto-append file changes to active task's context-*.md.

When Write or Edit tools modify files, this hook appends a brief change
record to the active task's context file. This provides incremental
context capture that survives context compaction.

Only fires when an active task exists in .claude/tasks/readme.md.
Skips if no active task is found.
"""

import json
import os
import re
import sys
from datetime import datetime


def find_active_task_dir(cwd: str) -> str | None:
    """Find the first active task directory from .claude/tasks/readme.md."""
    readme_path = os.path.join(cwd, ".claude", "tasks", "readme.md")
    if not os.path.isfile(readme_path):
        return None

    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    # Look for active task entries in the table
    # Format: | `dir_name/` | ... | status | ... |
    # Match directories in the Active section
    in_active = False
    for line in content.splitlines():
        if line.strip().startswith("## Active"):
            in_active = True
            continue
        if line.strip().startswith("## Completed"):
            in_active = False
            continue
        if not in_active:
            continue

        # Match directory names in table rows
        match = re.search(r"`([^`]+/)`", line)
        if match:
            dir_name = match.group(1)
            task_dir = os.path.join(cwd, ".claude", "tasks", dir_name)
            if os.path.isdir(task_dir):
                return task_dir

    return None


def get_or_create_context_file(task_dir: str) -> str:
    """Get today's context file or create a new one."""
    now = datetime.now()
    date_prefix = now.strftime("%Y%m%d")

    # Look for an existing active context file from today
    try:
        for fname in os.listdir(task_dir):
            if (
                fname.startswith(f"context-{date_prefix}")
                and fname.endswith(".md")
            ):
                # Check if it's still active
                fpath = os.path.join(task_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        head = f.read(200)
                    if "status: consumed" not in head:
                        return fpath
                except OSError:
                    continue
    except OSError:
        pass

    # Create a new context file
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    context_path = os.path.join(task_dir, f"context-{timestamp}-session.md")
    header = (
        "---\n"
        f"created: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "status: active\n"
        "tags: \"\"\n"
        "---\n\n"
    )
    try:
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(header)
    except OSError:
        pass

    return context_path


def format_change_entry(tool_name: str, tool_input: dict) -> str | None:
    """Format a change entry for the context file."""
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None

    # Skip temporary and internal paths
    if file_path.startswith("/tmp") or file_path.startswith("/dev"):
        return None

    # Skip context-*.md files themselves to avoid recursion
    basename = os.path.basename(file_path)
    if basename.startswith("context-") and basename.endswith(".md"):
        return None

    now = datetime.now().strftime("%H:%M")

    if tool_name == "Write":
        return f"- `{now}` **Write** `{file_path}`\n"
    elif tool_name == "Edit":
        old = tool_input.get("old_string", "")[:80].replace("\n", " ")
        new = tool_input.get("new_string", "")[:80].replace("\n", " ")
        return f"- `{now}` **Edit** `{file_path}` — `{old}` → `{new}`\n"

    return None


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        return

    tool_input = input_data.get("tool_input", {})
    cwd = input_data.get("cwd", os.getcwd())

    # Find active task
    task_dir = find_active_task_dir(cwd)
    if not task_dir:
        return

    # Format the change entry
    entry = format_change_entry(tool_name, tool_input)
    if not entry:
        return

    # Get or create context file
    context_path = get_or_create_context_file(task_dir)

    # Append the entry
    try:
        with open(context_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        return


if __name__ == "__main__":
    main()
