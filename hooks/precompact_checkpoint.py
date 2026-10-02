#!/usr/bin/env python3
"""PreCompact hook: save a checkpoint before context compaction.

Second line of defense — a safety net that captures modified files
and user decisions from the transcript tail before compaction
discards tool output.

Saves checkpoints to .claude/context-checkpoints/.
Also updates session_state.md in the active task directory for fast
session recovery. Both are read back into context right after compaction
by sessionstart_compact_restore.py.
"""

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import autocommit  # noqa: E402
from lib.agent_worktree import capture_suppressed  # noqa: E402
from lib.tasks import find_active_task_dir  # noqa: E402


def extract_modified_files(lines: list[str]) -> list[str]:
    """Extract file paths from Write/Edit tool calls in transcript lines."""
    files = set()
    # Match file_path values in JSON-like content
    pattern = re.compile(r'"file_path"\s*:\s*"([^"]+)"')
    for line in lines:
        for match in pattern.finditer(line):
            path = match.group(1)
            # Skip internal/temporary paths
            if not path.startswith("/tmp") and not path.startswith("/dev"):
                files.add(path)
    return sorted(files)


def extract_referenced_knowledge(lines: list[str]) -> list[str]:
    """Extract knowledge entry paths that were read during the session."""
    entries = set()
    pattern = re.compile(r'\.claude/knowledge/entries/[^\s"]+\.md')
    for line in lines:
        for match in pattern.finditer(line):
            entries.add(match.group(0))
    return sorted(entries)


MAX_DECISIONS = 10
DECISION_CHARS = 200
# Where a cut decision may end: sentence ends first, then clause breaks.
_BOUNDARIES = ("。", "！", "？", ". ", "! ", "? ", "\n", "、", ", ", "，")


def shorten(text: str, limit: int = DECISION_CHARS) -> str:
    """Cut text to limit characters at a sentence or clause boundary, marked with "…"."""
    text = text.strip()
    if len(text) <= limit:
        return text.replace("\n", " ")
    head = text[: limit - 1]
    floor = limit * 3 // 5  # never give up more than 40% for a cleaner cut
    cut = max((head.rfind(b) + len(b.rstrip()) for b in _BOUNDARIES), default=-1)
    if cut < floor:
        cut = len(head)
    return head[:cut].replace("\n", " ").rstrip() + "…"


def _turn_text(entry: dict) -> str:
    """The user's own text in a transcript turn ("" for tool results)."""
    # Claude Code transcripts nest the turn under "message"; older and
    # synthetic shapes put "content" at the top level.
    message = entry.get("message")
    if isinstance(message, dict) and "content" in message:
        content = message["content"]
    else:
        content = entry.get("content", "")
    if isinstance(content, list):
        # Text blocks only: tool_result blocks share the "user" type but
        # carry tool output. A tagged block (<system-reminder>, …) riding
        # along with the prompt is the harness's, not the user's.
        content = " ".join(
            block.get("text", "").strip() for block in content
            if isinstance(block, dict) and block.get("type", "text") == "text"
            and not block.get("text", "").lstrip().startswith("<")
        )
    return content.strip() if isinstance(content, str) else ""


def _question_answers(entry: dict) -> list[str]:
    """`[header] answer` for each AskUserQuestion answer carried by this turn."""
    result = entry.get("toolUseResult")
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        return []
    headers = {
        q.get("question"): q.get("header")
        for q in result.get("questions") or [] if isinstance(q, dict)
    }
    found = []
    for question, answer in result["answers"].items():
        if not isinstance(answer, str) or not answer.strip():
            continue
        label = headers.get(question) or question
        found.append(f"[{label}] {answer.strip()}")
    return found


def extract_user_decisions(lines: list[str]) -> list[str]:
    """The user's latest requests and AskUserQuestion answers, oldest first.

    Every prompt the user typed counts: requests in Japanese are mostly short
    imperatives (〜して, 進めて) that no keyword list catches, and the newest
    ones are what the session must not forget. Harness traffic is left out.
    """
    decisions = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(entry, dict):
            continue

        # Look for user messages (role: "user" or type: "human")
        role = entry.get("role", "")
        msg_type = entry.get("type", "")
        if role not in ("user", "human") and msg_type not in ("user", "human"):
            continue

        # Hook feedback, skill bodies (isMeta) and the compaction summary are
        # not the user's words.
        if entry.get("isMeta") or entry.get("isCompactSummary"):
            continue

        # The clearest decisions: answers picked in an AskUserQuestion dialog.
        # They arrive as a tool_result turn with the structured answers in
        # toolUseResult.
        answers = _question_answers(entry)
        if answers:
            decisions.extend(shorten(a) for a in answers)
            continue

        content = _turn_text(entry)
        if len(content) < 2:
            continue

        # Harness notices (<task-notification>, <command-name>, …) arrive as
        # user turns that start with a tag; interruptions as "[Request …]".
        if content.startswith("<") or content.startswith("[Request interrupted"):
            continue

        decisions.append(shorten(content))

    # Keep the newest occurrence of each, then the newest MAX_DECISIONS.
    latest = list(dict.fromkeys(reversed(decisions)))
    return list(reversed(latest[:MAX_DECISIONS]))



def read_todo_progress(task_dir: str) -> str:
    """Read todo.md and return a progress summary."""
    todo_path = os.path.join(task_dir, "todo.md")
    if not os.path.isfile(todo_path):
        return "unknown"

    try:
        with open(todo_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return "unknown"

    done = content.count("- [x]")
    in_progress = content.count("- [~]")
    pending = content.count("- [ ]")
    total = done + in_progress + pending
    if total == 0:
        return "no tasks"

    current = ""
    for line in content.splitlines():
        if "- [~]" in line:
            current = line.strip().lstrip("- [~]").strip()
            break

    progress = f"{done}/{total} completed"
    if current:
        progress += f", current: {current}"
    return progress


def update_session_state(
    task_dir: str,
    user_decisions: list[str],
    modified_files: list[str],
) -> None:
    """Update session_state.md in the active task directory."""
    now = datetime.now()
    dir_name = os.path.basename(task_dir.rstrip("/"))
    progress = read_todo_progress(task_dir)

    lines = [
        "---",
        f"updated: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"task_dir: {dir_name}",
        "---",
        "",
        "## Current State",
        f"- Progress: {progress}",
    ]

    if modified_files:
        recent = modified_files[-3:]  # last 3 files
        lines.append(f"- Recent files: {', '.join(os.path.basename(f) for f in recent)}")

    lines.append("")

    if user_decisions:
        lines.append("## Key Decisions This Session")
        for d in user_decisions[-5:]:
            lines.append(f"- {d}")
        lines.append("")

    state_path = os.path.join(task_dir, "session_state.md")
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def _commit_note(result: "autocommit.CommitResult") -> str:
    """Short systemMessage suffix describing the opt-in safety-net commit."""
    if result.status == "committed":
        return f" Auto-committed {len(result.files)} knowledge/tasks file(s)."
    if result.status == "blocked":
        return f" Auto-commit blocked by leak-scan ({len(result.findings)} finding(s))."
    if result.status == "error":
        return f" Auto-commit error: {result.reason}."
    return ""


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    transcript_path = input_data.get("transcript_path", "")
    session_id = input_data.get("session_id", "unknown")
    trigger = input_data.get("trigger", "auto")
    cwd = input_data.get("cwd", os.getcwd())

    # Skip entirely inside harness-generated agent worktrees (issue #17):
    # checkpoint, session_state, and the safety-net commit would all land
    # in the ephemeral worktree / agent branch and be misattributed.
    if capture_suppressed(cwd):
        return

    # Opt-in safety-net commit of knowledge/tasks (shared with the SessionEnd
    # hook). Runs independently of whether there is a transcript to checkpoint.
    commit_note = _commit_note(autocommit.run(cwd, "PreCompact"))

    if not transcript_path or not os.path.isfile(transcript_path):
        return

    # Read tail of transcript (last 200 lines)
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail_lines = all_lines[-200:]
    except OSError:
        return

    # Extract information
    modified_files = extract_modified_files(tail_lines)
    # Decisions come from the whole transcript: in a long session the tail
    # is almost all tool traffic and holds no user prompt.
    user_decisions = extract_user_decisions(all_lines)
    referenced_knowledge = extract_referenced_knowledge(tail_lines)

    # Skip if nothing meaningful to checkpoint
    if not modified_files and not user_decisions and not referenced_knowledge:
        return

    # Build checkpoint content
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")

    lines = [
        "---",
        f"session_id: {session_id}",
        f"created: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"trigger: {trigger}",
        "---",
        "",
    ]

    if modified_files:
        lines.append("## Modified Files")
        for f in modified_files:
            lines.append(f"- {f}")
        lines.append("")

    if user_decisions:
        lines.append("## User Decisions")
        for d in user_decisions:
            lines.append(f"- {d}")
        lines.append("")

    if referenced_knowledge:
        lines.append("## Referenced Knowledge (re-read on resume)")
        for k in referenced_knowledge:
            lines.append(f"- {k}")
        lines.append("")

    content = "\n".join(lines)

    # Save checkpoint
    checkpoint_dir = os.path.join(cwd, ".claude", "context-checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, f"{timestamp}.md")

    try:
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return

    # Update session_state.md in active task directory
    task_dir = find_active_task_dir(cwd)
    if task_dir:
        update_session_state(task_dir, user_decisions, modified_files)

    # The harness discards a PreCompact hook's systemMessage (the user does
    # not see it either; it stays for direct runs and tests), and PreCompact
    # cannot add context. The checkpoint and session_state.md
    # reach the model through sessionstart_compact_restore.py (SessionStart,
    # matcher "compact"), which reads them back right after compaction.
    state_note = ""
    if task_dir:
        state_note = f" Session state updated: {task_dir}/session_state.md."
    result = {
        "systemMessage": (
            f"Context checkpoint saved: {checkpoint_path} "
            f"({len(modified_files)} files, {len(user_decisions)} decisions,"
            f" {len(referenced_knowledge)} knowledge entries)."
            f"{state_note}{commit_note}"
            " ccmemo restores it into context after compaction."
        )
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
