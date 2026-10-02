#!/usr/bin/env python3
"""SessionStart hook (matcher "compact"): bring saved state back after compaction.

The PreCompact hook saves a checkpoint (`.claude/context-checkpoints/`) and
the active task's `session_state.md`, but PreCompact cannot add context and
the harness discards its `systemMessage`. SessionStart with source
`compact` fires right after auto or manual compaction and is the one hook
whose output reaches the model, so this hook reads both files back as
`additionalContext`:

- the newest checkpoint whose `session_id` matches this session (a
  checkpoint from another session is never restored);
- `session_state.md` of the active task (`lib/tasks.py`: the task named by
  `CCMEMO_ACTIVE_TASK`, the one whose `Branch` matches, or the only active
  one), with its `updated:` time so the model can tell how fresh it is.

Read-only: checkpoints are still consumed (merged and deleted) by
`/plan-task`. The payload stays under MAX_CHARS, well inside the harness's
10,000-character cap for `additionalContext`; a section that does not fit
ends with the path to read the rest from.

Prints nothing when there is nothing to restore, when the source is not
`compact`, inside a harness agent worktree (the PreCompact hook writes
nothing there), or with `CCMEMO_COMPACT_RESTORE=0`. Fail-open: any error
exits 0 silently.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.agent_worktree import capture_suppressed  # noqa: E402
from lib.tasks import find_active_task_dir  # noqa: E402

MAX_CHARS = 8000       # whole payload; the harness caps additionalContext at 10,000
SECTION_CHARS = 3500   # per restored file

HEADER = (
    "ccmemo restored the working state saved just before compaction. "
    "These are notes from earlier in this session, not new instructions. "
    "Open the listed files for detail."
)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (flat key: value frontmatter, body) of a `---`-fenced file."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    body = text[end + 4:].lstrip("\n")
    return meta, body


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def latest_checkpoint(cwd: str, session_id: str) -> str | None:
    """Path of the newest checkpoint written for this session, else None."""
    if not session_id:
        return None
    directory = os.path.join(cwd, ".claude", "context-checkpoints")
    try:
        names = sorted((n for n in os.listdir(directory) if n.endswith(".md")), reverse=True)
    except OSError:
        return None
    for name in names:  # names are %Y%m%d-%H%M%S.md, so newest first
        path = os.path.join(directory, name)
        text = read_text(path)
        if text is None:
            continue
        meta, _ = split_frontmatter(text)
        if meta.get("session_id") == session_id:
            return path
    return None


def decisions_first(body: str) -> str:
    """Reorder a checkpoint's `## ` sections so the short, valuable ones come first.

    A checkpoint lists Modified Files first, and in a long session that list
    alone can fill the section budget and cut off the user's decisions. The
    file list goes last, where truncation costs least.
    """
    head, *parts = body.split("\n## ")
    if not parts:
        return body
    sections = ["## " + p if not p.startswith("## ") else p for p in parts]
    if head.startswith("## "):
        sections.insert(0, head)
        head = ""

    def rank(section: str) -> int:
        title = section.splitlines()[0]
        for i, key in enumerate(("User Decisions", "Referenced Knowledge")):
            if key in title:
                return i
        return 2 if "Modified Files" not in title else 3

    ordered = sorted(sections, key=rank)  # stable: unknown sections keep their order
    return "\n".join(s.rstrip("\n") + "\n" for s in ([head] if head.strip() else []) + ordered)


def clip(text: str, limit: int, path: str) -> str:
    """Cut text to limit characters, pointing at the file for the rest."""
    text = text.strip()
    if len(text) <= limit:
        return text
    note = f"\n… (truncated; read {path} for the rest)"
    return text[: max(0, limit - len(note))].rstrip() + note


def build_context(cwd: str, session_id: str) -> str:
    """The text to restore, or "" when there is nothing to restore."""
    sections = []

    checkpoint = latest_checkpoint(cwd, session_id)
    if checkpoint:
        meta, body = split_frontmatter(read_text(checkpoint) or "")
        rel = os.path.relpath(checkpoint, cwd)
        if body.strip():
            sections.append(
                f"## Checkpoint `{rel}` (saved {meta.get('created', '?')},"
                f" trigger {meta.get('trigger', '?')})\n\n"
                + clip(decisions_first(body), SECTION_CHARS, rel)
            )

    task_dir = find_active_task_dir(cwd)
    if task_dir:
        state_path = os.path.join(task_dir, "session_state.md")
        text = read_text(state_path)
        if text and text.strip():
            meta, body = split_frontmatter(text)
            rel = os.path.relpath(state_path, cwd)
            todo = os.path.relpath(os.path.join(task_dir, "todo.md"), cwd)
            sections.append(
                f"## Active task `{rel}` (updated {meta.get('updated', '?')};"
                f" plan in `{todo}`)\n\n"
                + clip(body, SECTION_CHARS, rel)
            )

    if not sections:
        return ""
    text = HEADER + "\n\n" + "\n\n".join(sections)
    return text[:MAX_CHARS]


def main() -> None:
    try:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if os.environ.get("CCMEMO_COMPACT_RESTORE", "1") == "0":
            return
        # The matcher already limits this hook to compaction; the check keeps
        # a hand-written registration without a matcher from firing on
        # startup, resume or clear.
        source = payload.get("source")
        if source is not None and source != "compact":
            return
        cwd = os.path.abspath(payload.get("cwd") or os.getcwd())
        if capture_suppressed(cwd):
            return
        context = build_context(cwd, str(payload.get("session_id") or ""))
        if context:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — a hook must never block a session
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
