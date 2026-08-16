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
    CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN: How many minutes a knowledge-entry
        write suppresses the nudge (default: 45)
"""

import json
import os
import sys
import time

ENTRIES_DIR = os.path.join(".claude", "knowledge", "entries")


def get_threshold_bytes() -> int:
    """Return the context guard threshold in bytes."""
    kb = int(os.environ.get("CCMEMO_CONTEXT_GUARD_THRESHOLD_KB", "300"))
    return kb * 1024


def get_recent_write_window_s() -> int:
    """Return the suppression window after an entry write, in seconds."""
    minutes = int(os.environ.get("CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN", "45"))
    return minutes * 60


def has_recent_knowledge_write() -> bool:
    """Whether a knowledge entry in THIS project was written recently.

    Checks entry-file mtimes under .claude/knowledge/entries/ (cwd-relative,
    the directory the session records into). Deliberately NOT a transcript
    scan: a substring check suppressed on mere path *mentions* (the per-prompt
    auto-search injection alone contains entry paths, so the guard almost
    never fired), while an actual-tool-call check would miss the canonical
    subagent recording flow, whose Write happens in a separate transcript.
    File mtimes see every write path — direct edits, recording subagents and
    kb_graph CLI writes alike.
    """
    latest = 0.0
    for dirpath, _dirnames, filenames in os.walk(ENTRIES_DIR):
        for fn in filenames:
            if not fn.endswith(".md") or fn == "CLAUDE.md":
                continue
            try:
                latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
            except OSError:
                continue
    if not latest:
        return False
    return (time.time() - latest) < get_recent_write_window_s()


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
    if has_recent_knowledge_write():
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
