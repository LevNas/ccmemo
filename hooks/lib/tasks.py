"""Active task lookup shared by the capture, checkpoint and restore hooks."""

import os
import re


def find_active_task_dir(cwd: str) -> str | None:
    """Find the first active task directory from .claude/tasks/readme.md.

    The readme lists tasks in a table under `## Active`; the first row whose
    backquoted `dir_name/` exists under `.claude/tasks/` wins. Rows under
    `## Completed` are ignored.
    """
    readme_path = os.path.join(cwd, ".claude", "tasks", "readme.md")
    if not os.path.isfile(readme_path):
        return None

    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

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

        match = re.search(r"`([^`]+/)`", line)
        if match:
            dir_name = match.group(1)
            task_dir = os.path.join(cwd, ".claude", "tasks", dir_name)
            if os.path.isdir(task_dir):
                return task_dir

    return None
