"""Active task lookup shared by the capture, checkpoint and restore hooks.

The capture hook appends to the active task's context file, the PreCompact
hook writes its `session_state.md`, and the SessionStart(compact) hook reads
that back. Picking the wrong task is worse than picking none: the restore
points the model at an unrelated plan and the writes land in work that
belongs to someone or something else. So the lookup returns a task only when
it can tell which one this session is working on:

1. `CCMEMO_ACTIVE_TASK` names the task directory (explicit pointer).
2. The `Branch` column of the `## Active` table in `.claude/tasks/readme.md`
   lists fnmatch patterns; the first row matching the current branch wins.
   The branch is read from `cwd`, which follows a worktree.
3. Exactly one task is active and it names no branch: that task.
4. Otherwise none. `CCMEMO_ACTIVE_TASK_FALLBACK=first` restores the old
   behavior (the first active row whose directory exists).
"""

import fnmatch
import os
import re
import subprocess

TASKS_DIR = os.path.join(".claude", "tasks")


def current_branch(cwd: str) -> str | None:
    """Branch checked out in cwd (a worktree has its own), else None."""
    try:
        # symbolic-ref also names a branch with no commits yet; it fails on a
        # detached HEAD, which has no branch to match.
        proc = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = proc.stdout.strip()
    return branch if proc.returncode == 0 and branch else None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def active_rows(cwd: str) -> list[tuple[str, list[str]]]:
    """(task_dir, branch patterns) for each `## Active` row whose directory exists.

    The table's header decides which column is `Branch`; a table without one
    yields no patterns. Rows under `## Completed` are ignored.
    """
    readme_path = os.path.join(cwd, TASKS_DIR, "readme.md")
    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []

    rows = []
    in_active = False
    branch_col = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_active = stripped.startswith("## Active")
            branch_col = None
            continue
        if not in_active or not stripped.startswith("|"):
            continue

        cells = _cells(stripped)
        match = re.search(r"`([^`]+/)`", stripped)
        if not match:
            if branch_col is None and not set(stripped) <= set("|-: "):
                lowered = [c.lower() for c in cells]
                if "branch" in lowered:
                    branch_col = lowered.index("branch")
            continue

        task_dir = os.path.join(cwd, TASKS_DIR, match.group(1))
        if not os.path.isdir(task_dir):
            continue
        patterns = []
        if branch_col is not None and branch_col < len(cells):
            patterns = [p for p in re.split(r"[\s,]+", cells[branch_col].replace("`", ""))
                        if p and p not in ("-", "—")]
        rows.append((task_dir, patterns))
    return rows


def _named_task(cwd: str, name: str) -> str | None:
    name = name.strip()
    if not name:
        return None
    path = name if os.path.isabs(name) else os.path.join(cwd, TASKS_DIR, name)
    return os.path.join(os.path.normpath(path), "") if os.path.isdir(path) else None


def find_active_task_dir(cwd: str) -> str | None:
    """The task this session is working on, or None when it cannot tell."""
    named = os.environ.get("CCMEMO_ACTIVE_TASK", "")
    if named:
        return _named_task(cwd, named)

    rows = active_rows(cwd)
    if not rows:
        return None

    if any(patterns for _, patterns in rows):
        branch = current_branch(cwd)
        if branch:
            for task_dir, patterns in rows:
                if any(fnmatch.fnmatchcase(branch, p) for p in patterns):
                    return task_dir

    if len(rows) == 1 and not rows[0][1]:
        return rows[0][0]

    if os.environ.get("CCMEMO_ACTIVE_TASK_FALLBACK", "") == "first":
        return rows[0][0]
    return None
