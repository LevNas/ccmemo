"""Auto-commit knowledge/tasks as a safety net (cc* policy-core initiative, phase 2).

A SAFETY NET that COMPLEMENTS — never replaces — the manual session-wrap flow.
Opt-in only (CCMEMO_AUTOCOMMIT=1); it never pushes (push is a separate, future
opt-in gated on private-remote visibility). Shared by the SessionEnd and
PreCompact hooks so both commit knowledge/tasks the same way (DRY).

Gates, in order (any failing gate -> no commit):
    1. CCMEMO_AUTOCOMMIT set            -- opt-in; unset => no-op
    2. inside a git work tree           -- git available, real repo
    3. no merge/rebase/cherry-pick      -- never interrupt an in-progress op
    4. target pathspec has changes      -- .claude/knowledge, .claude/tasks only
    5. leak_scan clean (or warn mode)   -- block leak-prone shapes by default

Commit messages carry NO AI-attribution trailers (the ~/.claude/CLAUDE.md rule):
this module generates the message, so the trailer is simply never written.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from . import leak_scan

# Only these paths are ever staged. `git add -A` is deliberately never used so
# unrelated working-tree changes can never be swept into a safety-net commit.
TARGET_PATHS = (".claude/knowledge", ".claude/tasks")

_ENABLE_ENV = "CCMEMO_AUTOCOMMIT"
_ON_LEAK_ENV = "CCMEMO_AUTOCOMMIT_ON_LEAK"  # "block" (default) | "warn"
_TRUTHY = ("1", "true", "yes", "on")

# How many entry names to list in the commit body before summarising the rest.
_MAX_LISTED = 10

# In-progress git operations whose presence means we must not commit.
_OP_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")


@dataclass
class _Change:
    xy: str   # two-char porcelain status (e.g. " M", "A ", "??", "R ")
    path: str


@dataclass
class CommitResult:
    """Outcome of an auto-commit attempt.

    status is one of:
        disabled  -- opt-in env not set (the common, silent no-op)
        skipped   -- not a git tree, or a git op is in progress
        clean     -- no knowledge/tasks changes (session-wrap already committed)
        blocked   -- leak_scan findings under the default "block" mode
        committed -- a commit was created (findings may be non-empty in warn mode)
        error     -- git add/commit failed
    """

    status: str
    reason: str = ""
    files: list[str] = field(default_factory=list)
    findings: list[tuple[str, "leak_scan.Finding"]] = field(default_factory=list)


def _git(cwd: str, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        input=input_text,
    )


def _in_work_tree(cwd: str) -> bool:
    r = _git(cwd, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def _operation_in_progress(cwd: str) -> bool:
    """True if a merge/rebase/cherry-pick/revert is mid-flight (don't interrupt)."""
    r = _git(cwd, "rev-parse", "--git-dir")
    if r.returncode != 0:
        return False
    git_dir = r.stdout.strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(cwd, git_dir)
    return any(os.path.exists(os.path.join(git_dir, m)) for m in _OP_MARKERS)


def _changed_files(cwd: str) -> list[_Change]:
    """List changes under the target pathspec via porcelain (no add side effects).

    --untracked-files=all expands new files individually; without it git folds a
    brand-new directory into a single ``?? .claude/knowledge/`` entry, which would
    defeat per-file leak scanning and miscount the commit message.
    """
    r = _git(cwd, "status", "--porcelain", "--untracked-files=all", "--", *TARGET_PATHS)
    if r.returncode != 0:
        return []
    changes: list[_Change] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        xy, rest = line[:2], line[3:]
        # Renames are "old -> new"; stage both so the delete + add are recorded.
        if " -> " in rest:
            parts = [p.strip().strip('"') for p in rest.split(" -> ", 1)]
        else:
            parts = [rest.strip().strip('"')]
        for p in parts:
            if p:
                changes.append(_Change(xy, p))
    return changes


def _scan_files(cwd: str, paths: list[str]) -> list[tuple[str, "leak_scan.Finding"]]:
    """Run leak_scan over the current body of each existing (non-deleted) file."""
    findings: list[tuple[str, leak_scan.Finding]] = []
    for rel in paths:
        full = os.path.join(cwd, rel)
        if not os.path.isfile(full):
            continue  # deleted / non-regular -> no body to scan
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for finding in leak_scan.scan(text):
            findings.append((rel, finding))
    return findings


def _short_name(path: str) -> str:
    """Last two path segments — enough to tell entries/foo.md from taskdir/todo.md."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


def _build_message(paths: list[str], trigger: str) -> str:
    n = len(paths)
    noun = "file" if n == 1 else "files"
    subject = f"chore(ccmemo): checkpoint knowledge/tasks ({n} {noun}, {trigger})"
    names = [_short_name(p) for p in paths]
    body = [f"- {name}" for name in names[:_MAX_LISTED]]
    if n > _MAX_LISTED:
        body.append(f"- ...and {n - _MAX_LISTED} more")
    return subject + "\n\n" + "\n".join(body) + "\n"


def _enabled() -> bool:
    return os.environ.get(_ENABLE_ENV, "").strip().lower() in _TRUTHY


def run(cwd: str, trigger: str) -> CommitResult:
    """Attempt an opt-in safety-net commit of knowledge/tasks. Never pushes."""
    if not _enabled():
        return CommitResult("disabled", "CCMEMO_AUTOCOMMIT not set")
    if not _in_work_tree(cwd):
        return CommitResult("skipped", "not a git work tree")
    if _operation_in_progress(cwd):
        return CommitResult("skipped", "git operation in progress")

    changes = _changed_files(cwd)
    if not changes:
        return CommitResult("clean", "no knowledge/tasks changes")

    # Unique paths, order-preserving (a rename contributes both old and new).
    seen: set[str] = set()
    files = [c.path for c in changes if not (c.path in seen or seen.add(c.path))]

    findings = _scan_files(cwd, files)
    mode = (os.environ.get(_ON_LEAK_ENV, "block").strip().lower() or "block")
    if findings and mode != "warn":
        return CommitResult("blocked", f"leak-scan: {len(findings)} finding(s)", files, findings)

    add = _git(cwd, "add", "--", *files)
    if add.returncode != 0:
        return CommitResult("error", f"git add failed: {add.stderr.strip()}", files, findings)

    # No --no-verify: an existing pre-commit hook is respected (it is a second,
    # legitimate gate). If it blocks, the auto-commit legitimately fails.
    msg = _build_message(files, trigger)
    commit = _git(cwd, "commit", "-F", "-", input_text=msg)
    if commit.returncode != 0:
        return CommitResult("error", f"git commit failed: {commit.stderr.strip()}", files, findings)
    return CommitResult("committed", trigger, files, findings)


def format_findings(result: CommitResult) -> str:
    """One-block stderr summary of leak-scan findings for hook entry points."""
    lines = [
        f"ccmemo autocommit [{result.status}]: leak-scan flagged "
        f"{len(result.findings)} shape(s) in knowledge/tasks:"
    ]
    for rel, f in result.findings[:_MAX_LISTED]:
        lines.append(f"  {rel}:{f.lineno} [{f.kind}] {f.snippet}")
    if len(result.findings) > _MAX_LISTED:
        lines.append(f"  ...and {len(result.findings) - _MAX_LISTED} more")
    lines.append(
        "  -> review the files; set CCMEMO_AUTOCOMMIT_ON_LEAK=warn to commit anyway."
    )
    return "\n".join(lines) + "\n"
