#!/usr/bin/env python3
"""Self-tests for hooks/lib/tasks.py (active task lookup, #59).

Run: python3 tests/test_active_task.py   (exit 0 = all pass)

Each test builds a throwaway project with a `.claude/tasks/readme.md` and,
where the branch matters, a git repository. All fixture content is synthetic.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, HOOKS)
from lib.tasks import find_active_task_dir  # noqa: E402

PRECOMPACT = os.path.join(HOOKS, "precompact_checkpoint.py")
ENV_KEYS = ("CCMEMO_ACTIVE_TASK", "CCMEMO_ACTIVE_TASK_FALLBACK")

FAILURES = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILURES.append(name)
        if detail != "":
            print(f"     {detail!r}"[:400])


def lookup(project, **env):
    saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
    os.environ.update(env)
    try:
        found = find_active_task_dir(project)
    finally:
        for k in ENV_KEYS:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]
    return os.path.basename(found.rstrip("/")) if found else None


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-C", cwd, *args], check=True, capture_output=True)


def make_project(base, rows, header="| Directory | Issue | Status | Summary |",
                 branch=None, trailer="## Completed\n"):
    """rows: list of (dir_name, extra cells). branch: init git on this branch."""
    project = os.path.join(base, "project")
    tasks = os.path.join(project, ".claude", "tasks")
    os.makedirs(tasks, exist_ok=True)
    width = header.count("|") - 1
    lines = ["# Task Index", "", "## Active", "", header,
             "|" + "---|" * width]
    for dir_name, cells in rows:
        os.makedirs(os.path.join(tasks, dir_name), exist_ok=True)
        lines.append(f"| `{dir_name}/` | " + " | ".join(cells) + " |")
    lines += ["", trailer]
    with open(os.path.join(tasks, "readme.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if branch:
        git(project, "init", "-q", "-b", branch)
    return project


BRANCH_HEADER = "| Directory | Issue | Branch | Status | Summary |"


def test_branch_selects_matching_row():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [
            ("alpha", ["#1", "`feat/alpha-*`", "wip", "a"]),
            ("beta", ["#2", "`fix/beta-*`, `beta`", "wip", "b"]),
        ], header=BRANCH_HEADER, branch="fix/beta-restore")
        check("branch matches second row", lookup(project) == "beta", lookup(project))


def test_branch_matches_none():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [
            ("alpha", ["#1", "`feat/alpha-*`", "wip", "a"]),
            ("beta", ["#2", "`fix/beta-*`", "wip", "b"]),
        ], header=BRANCH_HEADER, branch="docs/unrelated")
        check("no matching branch: none", lookup(project) is None, lookup(project))
        check("fallback=first: first row",
              lookup(project, CCMEMO_ACTIVE_TASK_FALLBACK="first") == "alpha")


def test_single_unbound_row_without_git():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("only", ["—", "wip", "x"])])
        check("one active row, no Branch column: that row", lookup(project) == "only")


def test_single_row_bound_elsewhere():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("only", ["#1", "`feat/only`", "wip", "x"])],
                               header=BRANCH_HEADER, branch="main")
        check("one row bound to another branch: none", lookup(project) is None,
              lookup(project))


def test_several_rows_without_branch_column():
    """The first-row trap: an index with several active tasks and no Branch column."""
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("seed", ["—", "seed", "s"]),
                                      ("real", ["—", "wip", "r"])], branch="kb-x")
        check("several unbound rows: none", lookup(project) is None, lookup(project))


def test_explicit_pointer():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("seed", ["—", "seed", "s"]),
                                      ("real", ["—", "wip", "r"])])
        check("CCMEMO_ACTIVE_TASK names a dir", lookup(project, CCMEMO_ACTIVE_TASK="real/") == "real")
        check("CCMEMO_ACTIVE_TASK without slash", lookup(project, CCMEMO_ACTIVE_TASK="real") == "real")
        check("CCMEMO_ACTIVE_TASK missing dir: none",
              lookup(project, CCMEMO_ACTIVE_TASK="nope") is None)


def test_other_heading_ends_active_section():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("only", ["—", "wip", "x"])],
                               trailer="## Notes\n\n| `stray/` | — | — | — |\n")
        os.makedirs(os.path.join(project, ".claude", "tasks", "stray"))
        check("row under another heading is not active", lookup(project) == "only",
              lookup(project))


def test_branch_read_from_worktree_cwd():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [
            ("alpha", ["#1", "`feat/alpha`", "wip", "a"]),
            ("beta", ["#2", "`feat/beta`", "wip", "b"]),
        ], header=BRANCH_HEADER, branch="feat/alpha")
        with open(os.path.join(project, ".claude", "tasks", "alpha", "keep"), "w") as f:
            f.write("x")
        with open(os.path.join(project, ".claude", "tasks", "beta", "keep"), "w") as f:
            f.write("x")
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "init")
        worktree = os.path.join(base, "wt")
        git(project, "worktree", "add", "-q", "-b", "feat/beta", worktree)
        check("main checkout: alpha", lookup(project) == "alpha", lookup(project))
        check("worktree cwd: beta", lookup(worktree) == "beta", lookup(worktree))


def test_precompact_writes_no_unrelated_state():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base, [("seed", ["—", "seed", "s"]),
                                      ("real", ["—", "wip", "r"])], branch="kb-x")
        transcript = os.path.join(base, "t.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user",
                    "content": "PR #9 をマージして"}}, ensure_ascii=False) + "\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ENV_KEYS + ("CCMEMO_AUTOCOMMIT",)}
        subprocess.run([sys.executable, PRECOMPACT], input=json.dumps({
            "session_id": "s", "transcript_path": transcript, "cwd": project,
            "trigger": "auto", "hook_event_name": "PreCompact"}),
            capture_output=True, text=True, cwd=project, env=env, timeout=30)
        tasks = os.path.join(project, ".claude", "tasks")
        written = [d for d in ("seed", "real")
                   if os.path.exists(os.path.join(tasks, d, "session_state.md"))]
        check("precompact: no session_state.md in an unmatched task", written == [], written)
        checkpoints = os.listdir(os.path.join(project, ".claude", "context-checkpoints"))
        check("precompact: checkpoint still written", len(checkpoints) == 1, checkpoints)


def main():
    for t in sorted(k for k in globals() if k.startswith("test_")):
        globals()[t]()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
