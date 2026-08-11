#!/usr/bin/env python3
"""Dependency-free self-tests for hooks/lib/agent_worktree (issue #17).

Run: python3 tests/test_agent_worktree.py   (exit 0 = all pass)

Covers the tight-match detection (suppress only harness-generated agent /
workflow worktrees, fail toward capturing otherwise), the opt-out env var,
and an end-to-end check that the PostToolUse context writer skips capture
inside an agent worktree but still captures in a normal checkout.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib.agent_worktree import capture_suppressed, is_agent_worktree  # noqa: E402

HOOK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "hooks", "posttooluse_context_writer.py",
)

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    if actual == expected:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} — expected {expected!r}, got {actual!r}")
        FAILURES.append(name)


def make_task_tree(root: str) -> str:
    """Create a minimal .claude/tasks tree with one active task."""
    task_dir = os.path.join(root, ".claude", "tasks", "demo-task/")
    os.makedirs(task_dir, exist_ok=True)
    readme = os.path.join(root, ".claude", "tasks", "readme.md")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("# Task Index\n\n## Active\n\n| Directory |\n|---|\n| `demo-task/` |\n")
    return os.path.join(root, ".claude", "tasks", "demo-task")


def run_hook(cwd_path: str) -> None:
    # file_path must not start with /tmp (the writer skips temp paths);
    # a synthetic path is fine — only cwd decides where captures land.
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "/home/user/proj/x.py"},
        "cwd": cwd_path,
    })
    subprocess.run(
        [sys.executable, HOOK],
        input=payload, capture_output=True, text=True, check=True,
    )


def count_captures(task_dir: str) -> int:
    return len([f for f in os.listdir(task_dir) if f.startswith("context-")])


def main() -> None:
    repo = "/home/user/repo"
    wt = repo + "/.claude/worktrees"

    # --- Harness-generated worktrees: suppressed -------------------------
    check("agent worktree root",
          is_agent_worktree(f"{wt}/agent-a18749543825882f6"), True)
    check("agent worktree subdir",
          is_agent_worktree(f"{wt}/agent-a18749543825882f6/src/deep"), True)
    check("workflow worktree",
          is_agent_worktree(f"{wt}/wf_run-abc123-0"), True)
    check("workflow worktree subdir",
          is_agent_worktree(f"{wt}/wf_x9y-12/pkg"), True)

    # --- Fail-safe toward capturing ---------------------------------------
    check("user-named agent-foo keeps capturing",
          is_agent_worktree(f"{wt}/agent-foo"), False)
    check("short hex id keeps capturing",
          is_agent_worktree(f"{wt}/agent-abc123"), False)
    check("uppercase hex rejected (not harness convention)",
          is_agent_worktree(f"{wt}/agent-A18749543825882F6"), False)
    check("wf name without trailing index keeps capturing",
          is_agent_worktree(f"{wt}/wf_run-abc"), False)
    check("plain checkout",
          is_agent_worktree(repo + "/src"), False)
    check("named session worktree",
          is_agent_worktree(f"{wt}/my-feature"), False)
    check("worktrees dir not under .claude",
          is_agent_worktree("/home/user/worktrees/agent-a18749543825882f6"), False)
    check("empty path", is_agent_worktree(""), False)

    # --- Opt-out env var ---------------------------------------------------
    os.environ["CCMEMO_CAPTURE_AGENT_WORKTREES"] = "1"
    check("opt-out env restores capture",
          capture_suppressed(f"{wt}/agent-a18749543825882f6"), False)
    os.environ.pop("CCMEMO_CAPTURE_AGENT_WORKTREES")
    check("default: agent worktree suppressed",
          capture_suppressed(f"{wt}/agent-a18749543825882f6"), True)
    check("default: normal path not suppressed",
          capture_suppressed(repo), False)

    # --- End-to-end: context writer ----------------------------------------
    tmp = tempfile.mkdtemp(prefix="ccmemo-i17-")
    try:
        # Normal checkout: capture IS written
        normal = os.path.join(tmp, "repo")
        task_dir = make_task_tree(normal)
        run_hook(normal)
        check("e2e: normal checkout captures", count_captures(task_dir) > 0, True)

        # Agent worktree: capture is skipped
        agent_wt = os.path.join(
            tmp, "repo", ".claude", "worktrees", "agent-a18749543825882f6"
        )
        agent_task_dir = make_task_tree(agent_wt)
        run_hook(agent_wt)
        check("e2e: agent worktree suppressed", count_captures(agent_task_dir), 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("----")
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
