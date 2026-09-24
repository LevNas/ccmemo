#!/usr/bin/env python3
"""Repository resolution shared by the index, the search and the graph CLI.

One question, answered the same way everywhere: *which checkout owns the
index?* The answer is the **main checkout** — the working tree that holds
the real ``.git`` directory — found through ``git rev-parse
--git-common-dir`` from wherever the caller is. A linked worktree
(``git worktree add``) shares that directory, so every worktree resolves
to the same index file and none of them needs ``CCMEMO_KB_INDEX`` pinned
by hand.

Pure stdlib. Every helper fails soft (``None`` / empty) outside a git
repository or when git is missing, and the callers fall back to today's
cwd-relative behaviour.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache

_GIT_TIMEOUT = 10


def _git(cwd: str, *args: str) -> str | None:
    """stdout of ``git -C cwd args``, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


@lru_cache(maxsize=64)
def _rev_parse(start: str) -> tuple[str, str, str] | None:
    """(toplevel, git_dir, git_common_dir) as absolute paths, or None."""
    out = _git(start, "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) < 3:
        return None
    # A relative `--git-dir` / `--git-common-dir` is relative to the cwd git
    # ran in (`.git` at the toplevel, `../../.git` from a subdirectory).
    top = os.path.realpath(lines[0].strip())
    git_dir = os.path.realpath(os.path.join(start, lines[1].strip()))
    common = os.path.realpath(os.path.join(start, lines[2].strip()))
    return top, git_dir, common


def _start_dir(path: str) -> str:
    path = os.path.abspath(path)
    return path if os.path.isdir(path) else os.path.dirname(path)


def toplevel(path: str) -> str | None:
    """Working-tree root of the checkout containing *path* (a worktree's own root)."""
    info = _rev_parse(_start_dir(path))
    return info[0] if info else None


def main_checkout(path: str) -> str | None:
    """Root of the main checkout: the parent of the shared ``.git`` directory.

    Equal to :func:`toplevel` in the main checkout itself; from a linked
    worktree it points back at the checkout that owns ``.git``.
    """
    info = _rev_parse(_start_dir(path))
    if info is None:
        return None
    _top, _git_dir, common = info
    return os.path.dirname(common)


def is_linked_worktree(path: str) -> bool:
    """True when *path* lies in a ``git worktree add`` checkout, not the main one."""
    info = _rev_parse(_start_dir(path))
    if info is None:
        return False
    _top, git_dir, common = info
    return git_dir != common


def in_repo_relpath(path: str) -> str | None:
    """*path* relative to its checkout's toplevel (POSIX separators), or None."""
    top = toplevel(path)
    if top is None:
        return None
    rel = os.path.relpath(os.path.realpath(path), top)
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def ls_files(top: str) -> list[str] | None:
    """Files git knows under *top*: tracked, plus untracked files that are not
    ignored. POSIX relpaths, sorted. None when git cannot answer.

    ``--others --exclude-standard`` keeps an entry that was just written and
    not yet committed searchable (today's behaviour for the knowledge base)
    while ``.gitignore`` still drops the index DB, secrets and build output.
    Nested repositories and linked worktrees are not descended into: git
    lists them as a single ``dir/`` entry, which is filtered out below.
    """
    out = _git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if out is None:
        return None
    files = []
    for item in out.split("\0"):
        if not item or item.endswith("/"):
            continue
        files.append(item)
    return sorted(set(files))
