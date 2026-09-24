#!/usr/bin/env python3
"""SessionStart hook: refresh the search index in the background.

The hybrid search refreshes its index lazily, at search time, which is
correct but pays the embedding cost inside the first search of a session.
With `scope: repo` that first refresh can be long (a whole repository), so
this hook starts the same incremental refresh (`scripts/kb_index.py`) when
the session starts, detached and at low priority, and returns at once.
Lazy refresh stays the correctness layer; this only moves the cost earlier.

Runs only when all of these hold — otherwise it exits 0 without output:

- `.claude/knowledge/entries/` exists under the session's working directory;
- an index already exists (`.claude/knowledge/.index/kb.db`, or
  `$CCMEMO_KB_INDEX`): building one is an explicit opt-in (`uv run
  scripts/kb_index.py …`), never a side effect of starting a session;
- this is the main checkout, not a linked git worktree (worktrees read the
  main checkout's index and never write it) and not a harness agent worktree;
- `uv` is on PATH;
- no refresh started by an earlier session is still running (a lock file
  under `.index/` holds its pid).

Output of the refresh goes to `.index/prewarm.log`. Opt out with
`CCMEMO_INDEX_PREWARM=0`. Fail-open: any error exits 0 silently.
"""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.agent_worktree import is_agent_worktree  # noqa: E402
from lib import repo as _repo  # noqa: E402

KB_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "kb_index.py")
LOCK_NAME = "prewarm.lock"
LOG_NAME = "prewarm.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_held(lock_path: str) -> bool:
    try:
        with open(lock_path, encoding="utf-8") as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return False
    return pid > 0 and _pid_alive(pid)


def plan(cwd: str, env: dict) -> tuple[str, str, str] | None:
    """(entries root, index dir, lock path) when a refresh should start, else None."""
    if env.get("CCMEMO_INDEX_PREWARM", "1") == "0":
        return None
    root = os.path.join(cwd, ".claude", "knowledge", "entries")
    if not os.path.isdir(root) or not os.path.isfile(KB_INDEX):
        return None
    if is_agent_worktree(cwd) or _repo.is_linked_worktree(cwd):
        return None
    if not shutil.which("uv"):
        return None
    override = env.get("CCMEMO_KB_INDEX", "").strip()
    db = os.path.abspath(os.path.expanduser(override)) if override else \
        os.path.join(cwd, ".claude", "knowledge", ".index", "kb.db")
    if not os.path.isfile(db):
        return None
    index_dir = os.path.dirname(db)
    lock = os.path.join(index_dir, LOCK_NAME)
    if _lock_held(lock):
        return None
    return root, index_dir, lock


def start(root: str, index_dir: str, lock: str) -> None:
    log = open(os.path.join(index_dir, LOG_NAME), "ab")
    nice = ["nice", "-n", "10"] if shutil.which("nice") else []
    cmd = [*nice, "uv", "run", os.path.abspath(KB_INDEX), root]
    # The child owns the lock: it writes its pid and removes the file when
    # the refresh ends, whatever the outcome.
    script = ("echo $$ > \"$0\"; \"${@:1}\"; rc=$?; rm -f \"$0\"; exit $rc")
    proc = subprocess.Popen(
        ["bash", "-c", script, lock, *cmd],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, cwd=os.path.dirname(root),
    )
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    log.close()


def main() -> None:
    try:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError):
            payload = {}
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        cwd = os.path.abspath(cwd or os.getcwd())
        todo = plan(cwd, os.environ)
        if todo is not None:
            start(*todo)
    except Exception:  # noqa: BLE001 — a hook must never block a session
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
