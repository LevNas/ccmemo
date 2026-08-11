#!/usr/bin/env python3
"""Dependency-free self-tests for hooks/sessionend_tasks_mirror.py (issue #18).

Run: python3 tests/test_tasks_mirror.py   (exit 0 = all pass)

Each case builds a throwaway git repo with a linked worktree and runs the
hook as a subprocess, exactly as the harness would.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "hooks", "sessionend_tasks_mirror.py",
)

FAILURES: list[str] = []


def check(name: str, cond: bool) -> None:
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")
        FAILURES.append(name)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    )


def make_repo(base, gitignore_tasks=True):
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("test\n")
    if gitignore_tasks:
        with open(os.path.join(repo, ".gitignore"), "w") as f:
            f.write(".claude/tasks/\n.claude/worktrees/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def add_worktree(repo, path, branch):
    _git(repo, "worktree", "add", "-q", path, "-b", branch)
    return path


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def run_hook(cwd, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"cwd": cwd, "hook_event_name": "SessionEnd"}),
        capture_output=True, text=True, env=env, check=True,
    )


def main() -> None:
    base = tempfile.mkdtemp(prefix="ccmemo-i18-")
    try:
        # --- issue-centric repo with a linked worktree ---------------------
        repo = make_repo(base)
        wt = add_worktree(repo, os.path.join(base, "wt-alpha"), "t1")
        main_tasks = os.path.join(repo, ".claude", "tasks")

        # 1. new file is copied
        write(os.path.join(wt, ".claude", "tasks", "demo", "note.md"), "v1\n")
        run_hook(wt)
        target = os.path.join(main_tasks, "demo", "note.md")
        check("new file copied to main checkout",
              os.path.isfile(target) and open(target).read() == "v1\n")

        # 2. identical file is skipped (no shadow copy appears)
        run_hook(wt)
        shadow = os.path.join(main_tasks, "demo", "note-from-wt-alpha.md")
        check("identical file skipped (no shadow)", not os.path.exists(shadow))

        # 3. differing target: shadow copy, original untouched
        write(os.path.join(main_tasks, "demo", "note.md"), "main-edit\n")
        write(os.path.join(wt, ".claude", "tasks", "demo", "note.md"), "v2\n")
        run_hook(wt)
        check("differing target untouched",
              open(target).read() == "main-edit\n")
        check("shadow copy written with worktree suffix",
              os.path.isfile(shadow) and open(shadow).read() == "v2\n")

        # 4. shadow slot refreshes on a later run
        write(os.path.join(wt, ".claude", "tasks", "demo", "note.md"), "v3\n")
        run_hook(wt)
        check("shadow slot refreshed", open(shadow).read() == "v3\n")

        # 5. opt-out env
        write(os.path.join(wt, ".claude", "tasks", "demo", "extra.md"), "x\n")
        run_hook(wt, {"CCMEMO_TASKS_MIRROR": "0"})
        check("opt-out env disables mirroring",
              not os.path.exists(os.path.join(main_tasks, "demo", "extra.md")))

        # 6. main checkout (not a worktree) is a no-op
        write(os.path.join(repo, ".claude", "tasks", "demo", "own.md"), "y\n")
        before = set(os.listdir(os.path.join(main_tasks, "demo")))
        run_hook(repo)
        after = set(os.listdir(os.path.join(main_tasks, "demo")))
        check("main checkout no-op", before == after)

        # 7. agent worktree is skipped
        agent_wt = add_worktree(
            repo,
            os.path.join(repo, ".claude", "worktrees", "agent-a18749543825882f6"),
            "t2",
        )
        write(os.path.join(agent_wt, ".claude", "tasks", "demo", "leaf.md"), "z\n")
        run_hook(agent_wt)
        check("agent worktree skipped",
              not os.path.exists(os.path.join(main_tasks, "demo", "leaf.md")))

        # --- git-tracked repo (tasks NOT ignored) ---------------------------
        base2 = os.path.join(base, "tracked")
        os.makedirs(base2)
        repo2 = make_repo(base2, gitignore_tasks=False)
        wt2 = add_worktree(repo2, os.path.join(base2, "wt-beta"), "t1")
        write(os.path.join(wt2, ".claude", "tasks", "demo", "note.md"), "t\n")
        run_hook(wt2)
        check("git-tracked mode no-op (commits are the transport)",
              not os.path.exists(
                  os.path.join(repo2, ".claude", "tasks", "demo", "note.md")))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("----")
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
