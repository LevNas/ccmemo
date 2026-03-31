#!/usr/bin/env python3
"""PreCompact hook: save a checkpoint before context compaction.

Second line of defense — a safety net that captures modified files
and user decisions from the transcript tail before compaction
discards tool output.

Saves checkpoints to .claude/context-checkpoints/.
Also updates session_state.md in the active task directory for fast
session recovery.
"""

import json
import os
import re
import sys
from datetime import datetime


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


def extract_user_decisions(lines: list[str]) -> list[str]:
    """Extract user messages that look like decisions or plans."""
    decisions = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Look for user messages (role: "user" or type: "human")
        role = entry.get("role", "")
        msg_type = entry.get("type", "")
        if role not in ("user", "human") and msg_type not in ("user", "human"):
            continue

        # Extract text content
        content = entry.get("content", "")
        if isinstance(content, list):
            # Handle structured content blocks
            texts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        texts.append(text)
            content = " ".join(texts)

        if not isinstance(content, str) or len(content) < 10:
            continue

        # Heuristic: lines with decision-like keywords
        lower = content.lower()
        decision_keywords = [
            "にする", "にした", "方針", "計画", "プラン", "決定",
            "採用", "変更", "修正", "追加", "削除", "移行",
            "decide", "plan", "approach", "strategy", "change",
            "let's", "we should", "i want", "please",
        ]
        if any(kw in lower for kw in decision_keywords):
            # Truncate long messages
            snippet = content[:200].replace("\n", " ").strip()
            if snippet:
                decisions.append(snippet)

    # Deduplicate and limit
    seen = set()
    unique = []
    for d in decisions:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique[:10]


def find_active_task_dir(cwd: str) -> str | None:
    """Find the first active task directory from .claude/tasks/readme.md."""
    readme_path = os.path.join(cwd, ".claude", "tasks", "readme.md")
    if not os.path.isfile(readme_path):
        return None

    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    in_active = False
    for line in content.splitlines():
        if line.strip().startswith("## Active"):
            in_active = True
            continue
        if line.strip().startswith("## Completed"):
            in_active = False
            continue
        if not in_active:
            continue

        match = re.search(r"`([^`]+/)`", line)
        if match:
            dir_name = match.group(1)
            task_dir = os.path.join(cwd, ".claude", "tasks", dir_name)
            if os.path.isdir(task_dir):
                return task_dir

    return None


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
        for d in user_decisions[:5]:
            lines.append(f"- {d}")
        lines.append("")

    state_path = os.path.join(task_dir, "session_state.md")
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    transcript_path = input_data.get("transcript_path", "")
    session_id = input_data.get("session_id", "unknown")
    trigger = input_data.get("trigger", "auto")
    cwd = input_data.get("cwd", os.getcwd())

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
    user_decisions = extract_user_decisions(tail_lines)
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

    # Output system message (may be included in compaction summary)
    knowledge_note = ""
    if referenced_knowledge:
        knowledge_note = (
            " Re-read these knowledge entries: "
            + ", ".join(referenced_knowledge)
        )
    state_note = ""
    if task_dir:
        state_note = f" Session state updated: {task_dir}/session_state.md."
    result = {
        "systemMessage": (
            f"Context checkpoint saved: {checkpoint_path} "
            f"({len(modified_files)} files, {len(user_decisions)} decisions)."
            f"{knowledge_note}{state_note}"
            " On resume: run TaskList, then read session_state.md for quick recovery."
        )
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
