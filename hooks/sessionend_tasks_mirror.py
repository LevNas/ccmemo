#!/usr/bin/env python3
"""SessionEnd hook: mirror worktree-local .claude/tasks/ back to the main checkout.

In issue-centric mode `.claude/tasks/` is gitignored (multi-user repos keep
per-worker scratchpads out of commits), so commits cannot transport task
files out of a linked worktree — anything written there dies with the
worktree. This hook copies task files back to the main checkout when a
session that ran inside a linked worktree ends (issue #18).

Rules:
- Runs only when `.claude/tasks` is actually gitignored in the worktree —
  in git-tracked mode commits are the transport and mirroring would just
  dirty the main checkout.
- Copy-only: never deletes or overwrites on either side. Byte-identical
  targets are skipped; a differing existing target gets the copy written
  alongside as `<name>-from-<worktree-basename><ext>` (that suffixed slot
  belongs to this worktree and is refreshed on later runs).
- Harness-generated agent worktrees are skipped entirely (leaves must not
  write `.claude/tasks/`; capture there is already suppressed — see #17).
- Fail-open: any error exits 0 silently.

Opt-out: CCMEMO_TASKS_MIRROR=0
"""

import filecmp
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.agent_worktree import is_agent_worktree  # noqa: E402


def _git(cwd, *args):
    """Run git in cwd; return CompletedProcess or None on failure to spawn."""
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def linked_worktree_roots(cwd):
    """Return (worktree_root, main_checkout) if cwd is inside a linked
    worktree, else None."""
    out = _git(cwd, "rev-parse", "--git-dir", "--git-common-dir")
    if out is None or out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    if len(lines) != 2:
        return None
    git_dir, common_dir = (
        os.path.abspath(os.path.join(cwd, p)) for p in lines
    )
    if git_dir == common_dir:
        return None  # main checkout, not a linked worktree

    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None or toplevel.returncode != 0:
        return None
    wt_root = toplevel.stdout.strip()
    main_checkout = os.path.dirname(common_dir)
    return wt_root, main_checkout


def tasks_gitignored(wt_root):
    """True only when .claude/tasks is ignored (issue-centric mode)."""
    out = _git(wt_root, "check-ignore", "-q", "--", ".claude/tasks")
    return out is not None and out.returncode == 0


def mirror(src_root, dst_root, suffix):
    """Copy src_root into dst_root without overwriting differing targets."""
    copied, shadowed = [], []
    for dirpath, _dirnames, filenames in os.walk(src_root):
        rel_dir = os.path.relpath(dirpath, src_root)
        for fn in sorted(filenames):
            src = os.path.join(dirpath, fn)
            rel = os.path.normpath(os.path.join(rel_dir, fn))
            dst = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied.append(rel)
            elif filecmp.cmp(src, dst, shallow=False):
                continue
            else:
                stem, ext = os.path.splitext(dst)
                alt = f"{stem}-from-{suffix}{ext}"
                if os.path.exists(alt) and filecmp.cmp(src, alt, shallow=False):
                    continue
                shutil.copy2(src, alt)
                shadowed.append(os.path.relpath(alt, dst_root))
    return copied, shadowed


def main():
    if os.environ.get("CCMEMO_TASKS_MIRROR") == "0":
        return
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return
    cwd = input_data.get("cwd", os.getcwd())

    if is_agent_worktree(cwd):
        return

    roots = linked_worktree_roots(cwd)
    if not roots:
        return
    wt_root, main_checkout = roots

    src_root = os.path.join(wt_root, ".claude", "tasks")
    dst_root = os.path.join(main_checkout, ".claude", "tasks")
    if not os.path.isdir(src_root):
        return
    if os.path.realpath(src_root) == os.path.realpath(dst_root):
        return
    if not tasks_gitignored(wt_root):
        return  # git-tracked mode: commits are the transport

    try:
        copied, shadowed = mirror(
            src_root, dst_root, os.path.basename(wt_root)
        )
    except OSError:
        return

    if copied or shadowed:
        print(json.dumps({"systemMessage": (
            f"[ccmemo] tasks mirror: {len(copied)} copied, "
            f"{len(shadowed)} shadow-copied to {dst_root}"
        )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
