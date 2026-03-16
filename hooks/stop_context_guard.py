#!/usr/bin/env python3
"""Stop hook: prompt user to record knowledge when context grows large.

First line of defense against context loss during compaction.
When the transcript exceeds a size threshold and no knowledge entry
has been recorded recently, blocks the stop with a prompt to run
/record-knowledge.

Environment:
    CCMEMO_CONTEXT_GUARD_THRESHOLD_KB: Size threshold in KB (default: 300)
"""

import json
import os
import sys


def get_threshold_bytes() -> int:
    """Return the context guard threshold in bytes."""
    kb = int(os.environ.get("CCMEMO_CONTEXT_GUARD_THRESHOLD_KB", "300"))
    return kb * 1024


def has_recent_knowledge_write(transcript_path: str) -> bool:
    """Check if a knowledge entry was written recently in the transcript.

    Scans the last portion of the transcript for Write/Edit tool calls
    targeting .claude/knowledge/entries/.
    """
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return False

    # Read last 100KB of the transcript to check for recent writes
    read_size = min(size, 100 * 1024)
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            if size > read_size:
                f.seek(size - read_size)
            tail = f.read()
    except OSError:
        return False

    return ".claude/knowledge/entries/" in tail


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # Cannot parse input — allow stop
        print("{}")
        return

    # Prevent infinite loop: if stop hook is already active, allow stop
    if input_data.get("stop_hook_active"):
        print("{}")
        return

    transcript_path = input_data.get("transcript_path", "")
    if not transcript_path:
        print("{}")
        return

    # Check transcript size
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        print("{}")
        return

    threshold = get_threshold_bytes()
    if size < threshold:
        print("{}")
        return

    # Check if knowledge was already recorded
    if has_recent_knowledge_write(transcript_path):
        print("{}")
        return

    # Block and prompt user
    size_kb = size // 1024
    result = {
        "decision": "block",
        "reason": (
            f"コンテキストが増大しています（約{size_kb}KB）。"
            "/record-knowledge で知見を記録しますか？"
            "不要なら「不要」と答えてください。"
        ),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
