#!/usr/bin/env python3
"""Stop hook: prompt the MODEL to self-assess whether to record knowledge.

First line of defense against context loss during compaction. When the
transcript exceeds a size threshold and no knowledge entry has been
recorded recently, blocks ONCE so the model can decide whether the session
produced knowledge worth recording (design decisions, pitfalls, fixes).

Design (SPEC: 判断=LLM / 決定論=hook): the hook only detects the *moment*
(big context + nothing recorded). The reason returns to the MODEL — it is
not a yes/no question to the user — so the model either invokes
record-knowledge / session-wrap, or ends the session when only routine work
happened (avoids the npm-install-only false block). stop_hook_active
guarantees the second stop is allowed, so this is a single self-assessment
turn with no loop, and recording still goes through the explicit skills
(no auto-memory).

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

    # Block once so the MODEL self-assesses. The reason returns to the model
    # (not a question to the user); stop_hook_active allows the 2nd stop.
    size_kb = size // 1024
    result = {
        "decision": "block",
        "reason": (
            f"セッションが長くなっています（約{size_kb}KB）。"
            "記録価値のある知見（設計決定・落とし穴・課題解決・"
            "私の誤りの指摘）があれば record-knowledge または "
            "session-wrap で記録してください。"
            "単純作業のみで記録不要なら、このまま終了して構いません。"
        ),
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": (
                "知見記録の判断基準（すべて Yes のときのみ記録、"
                "auto-memory 化を避ける）: "
                "①3か月後の自分が参照したいか "
                "②別マシン/別プロジェクトで再発しうるか "
                "③コードや git log だけからは復元できない暗黙知か。"
            ),
        },
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
