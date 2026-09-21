#!/usr/bin/env python3
"""Dependency-free self-tests for checkout-suffixed capture filenames (issue #24).

Run: python3 tests/test_checkout_id.py   (exit 0 = all pass)

Covers hooks/lib/checkout_id (digest shape, stability, git toplevel vs cwd
fallback, no hostname/path leakage) and the PostToolUse context writer end to
end: unchanged behavior without CCMEMO_CAPTURE_CHECKOUT_SUFFIX, suffixed
filenames with it, and reuse limited to this checkout's own unconsumed file.
All hostnames and paths in fixtures are fictional.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib.checkout_id import checkout_id, derive_id, suffix_enabled  # noqa: E402

HOOK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "hooks", "posttooluse_context_writer.py",
)
ENV_VAR = "CCMEMO_CAPTURE_CHECKOUT_SUFFIX"
LEGACY_RE = re.compile(r"^context-\d{8}-\d{6}-session\.md$")
SUFFIXED_RE = re.compile(r"^context-\d{8}-\d{6}-session-([0-9a-f]{8})\.md$")

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    if actual == expected:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} — expected {expected!r}, got {actual!r}")
        FAILURES.append(name)


def make_task_tree(root: str) -> str:
    """Create a minimal .claude/tasks tree with one active task."""
    task_dir = os.path.join(root, ".claude", "tasks", "demo-task")
    os.makedirs(task_dir, exist_ok=True)
    readme = os.path.join(root, ".claude", "tasks", "readme.md")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("# Task Index\n\n## Active\n\n| Directory |\n|---|\n| `demo-task/` |\n")
    return task_dir


def run_hook(cwd_path: str, suffix: str | None, target: str = "/home/user/proj/x.py") -> str:
    env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
    if suffix is not None:
        env[ENV_VAR] = suffix
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": target},
        "cwd": cwd_path,
    })
    out = subprocess.run(
        [sys.executable, HOOK],
        input=payload, capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout + out.stderr


def captures(task_dir: str) -> list[str]:
    return sorted(f for f in os.listdir(task_dir) if f.startswith("context-"))


def read(task_dir: str, name: str) -> str:
    with open(os.path.join(task_dir, name), encoding="utf-8") as f:
        return f.read()


def git_init(path: str) -> None:
    subprocess.run(["git", "-C", path, "init", "-q"], check=True, capture_output=True)


def main() -> None:
    # --- digest --------------------------------------------------------------
    expected = hashlib.sha256(b"host-a.example\0/srv/checkouts/demo").hexdigest()[:8]
    check("derive_id = sha256(host NUL root)[:8]",
          derive_id("host-a.example", "/srv/checkouts/demo"), expected)
    check("derive_id differs by host",
          derive_id("host-b.example", "/srv/checkouts/demo") != expected, True)
    check("derive_id differs by path",
          derive_id("host-a.example", "/srv/checkouts/demo-wt") != expected, True)
    check("NUL separator prevents boundary collisions",
          derive_id("host-a", "b/x") != derive_id("host-ab", "/x"), True)

    saved = os.environ.pop(ENV_VAR, None)
    try:
        check("suffix disabled by default", suffix_enabled(), False)
        os.environ[ENV_VAR] = "true"
        check("only the literal 1 enables", suffix_enabled(), False)
        os.environ[ENV_VAR] = "1"
        check("suffix enabled with 1", suffix_enabled(), True)
    finally:
        os.environ.pop(ENV_VAR, None)
        if saved is not None:
            os.environ[ENV_VAR] = saved

    tmp = tempfile.mkdtemp(prefix="ccmemo-i24-")
    try:
        # --- checkout_id: git toplevel vs cwd fallback -----------------------
        repo = os.path.join(tmp, "repo")
        sub = os.path.join(repo, "src", "deep")
        os.makedirs(sub)
        git_init(repo)
        check("id is 8 lowercase hex",
              bool(re.fullmatch(r"[0-9a-f]{8}", checkout_id(repo))), True)
        check("same id from a subdirectory (git toplevel)",
              checkout_id(sub), checkout_id(repo))
        check("id matches the documented formula",
              checkout_id(repo),
              derive_id(socket.gethostname(), os.path.realpath(repo)))
        link = os.path.join(tmp, "repo-link")
        os.symlink(repo, link)
        check("symlinked path resolves to the same id (realpath)",
              checkout_id(link), checkout_id(repo))

        other = os.path.join(tmp, "other-checkout")
        os.makedirs(other)
        git_init(other)
        check("a second checkout gets a different id",
              checkout_id(other) != checkout_id(repo), True)

        plain = os.path.join(tmp, "no-git")
        os.makedirs(os.path.join(plain, "a"))
        outside_git = subprocess.run(
            ["git", "-C", plain, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True).returncode != 0
        if outside_git:
            check("outside git: realpath of cwd",
                  checkout_id(plain),
                  derive_id(socket.gethostname(), os.path.realpath(plain)))
            check("outside git: cwd-specific",
                  checkout_id(os.path.join(plain, "a")) != checkout_id(plain), True)
        else:
            print("SKIP: tempdir lies inside a git work tree — cwd fallback not exercised")

        # --- e2e: env unset → unchanged behavior -----------------------------
        legacy_repo = os.path.join(tmp, "legacy")
        legacy_tasks = make_task_tree(legacy_repo)
        git_init(legacy_repo)
        run_hook(legacy_repo, None)
        names = captures(legacy_tasks)
        check("unset: one capture file", len(names), 1)
        check("unset: legacy filename", bool(LEGACY_RE.match(names[0])), True)
        run_hook(legacy_repo, None, "/home/user/proj/y.py")
        check("unset: second write reuses the file", captures(legacy_tasks), names)
        check("unset: both entries appended",
              read(legacy_tasks, names[0]).count("**Write**"), 2)
        run_hook(legacy_repo, "0")
        check("=0: still legacy behavior", captures(legacy_tasks), names)

        # unset + a foreign suffixed file of today: legacy rule reuses any of
        # today's unconsumed files — exactly as before this feature existed
        today = datetime.now().strftime("%Y%m%d")
        mixed_repo = os.path.join(tmp, "mixed")
        mixed_tasks = make_task_tree(mixed_repo)
        foreign = f"context-{today}-000001-session-0123abcd.md"
        with open(os.path.join(mixed_tasks, foreign), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\n---\n\n")
        run_hook(mixed_repo, None)
        check("unset: legacy reuse rule untouched by suffixed names",
              captures(mixed_tasks), [foreign])

        # --- e2e: env=1 → suffixed name, own-file-only reuse -------------------
        on_repo = os.path.join(tmp, "suffixed")
        on_tasks = make_task_tree(on_repo)
        git_init(on_repo)
        own_id = checkout_id(on_repo)
        legacy_today = f"context-{today}-000001-session.md"
        foreign_body = "---\nstatus: active\n---\n\n- foreign line\n"
        for name in (legacy_today, foreign):
            with open(os.path.join(on_tasks, name), "w", encoding="utf-8") as f:
                f.write(foreign_body)

        output = run_hook(on_repo, "1")
        mine = [n for n in captures(on_tasks) if n not in (legacy_today, foreign)]
        check("=1: a new file is created next to foreign ones", len(mine), 1)
        m = SUFFIXED_RE.match(mine[0]) if mine else None
        check("=1: filename ends in -session-<id8>.md", bool(m), True)
        check("=1: id8 is this checkout's id", m.group(1) if m else None, own_id)
        check("=1: other checkouts' files are not appended to",
              [read(on_tasks, legacy_today), read(on_tasks, foreign)],
              [foreign_body, foreign_body])

        run_hook(on_repo, "1", "/home/user/proj/y.py")
        check("=1: second write reuses the own file",
              [n for n in captures(on_tasks) if n not in (legacy_today, foreign)], mine)
        check("=1: both entries in the own file",
              read(on_tasks, mine[0]).count("**Write**"), 2)

        # no hostname / checkout path in the filename or body
        host = socket.gethostname()
        body = read(on_tasks, mine[0])
        check("=1: hostname absent from filename and body",
              host in mine[0] or (len(host) > 2 and host in body), False)
        check("=1: checkout path absent from body",
              os.path.realpath(on_repo) in body, False)
        check("=1: hook output names the digest only, never the hostname",
              len(host) > 2 and host in output.replace(on_repo, ""), False)

        # a consumed own file is not reused
        path = os.path.join(on_tasks, mine[0])
        with open(path, "w", encoding="utf-8") as f:
            f.write(body.replace("status: active", "status: consumed"))
        os.rename(path, os.path.join(on_tasks, f"context-{today}-000002-session-{own_id}.md"))
        run_hook(on_repo, "1", "/home/user/proj/z.py")
        fresh = [n for n in captures(on_tasks)
                 if n.endswith(f"-session-{own_id}.md") and "-000002-" not in n]
        check("=1: consumed own file → a fresh one is created", len(fresh), 1)

        # tasks-mirror shadow copies (<name>-from-<worktree>.md) are foreign too
        shadow_repo = os.path.join(tmp, "shadow")
        shadow_tasks = make_task_tree(shadow_repo)
        git_init(shadow_repo)
        shadow = f"context-{today}-000001-session-{checkout_id(shadow_repo)}-from-wt.md"
        with open(os.path.join(shadow_tasks, shadow), "w", encoding="utf-8") as f:
            f.write(foreign_body)
        run_hook(shadow_repo, "1")
        check("=1: mirror shadow copy is not appended to",
              read(shadow_tasks, shadow), foreign_body)

        # writes to suffixed capture files themselves are still skipped
        before = captures(on_tasks)
        sizes = [os.path.getsize(os.path.join(on_tasks, n)) for n in before]
        # (synthetic non-/tmp path: the writer skips temp paths for another reason)
        run_hook(on_repo, "1",
                 f"/home/user/proj/.claude/tasks/t/context-{today}-000003-session-0123abcd.md")
        check("=1: no recursion on suffixed context files",
              [os.path.getsize(os.path.join(on_tasks, n)) for n in captures(on_tasks)],
              sizes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("----")
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
