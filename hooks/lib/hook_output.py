"""Output shapes for hook events."""

import json
import sys


def post_tool_use_context(message: str, stream=None) -> None:
    """Tell Claude something after a tool ran, without blocking.

    PostToolUse accepts only "block" as a decision. Any other value, such as
    "warn", is reported as a hook error and its reason never reaches Claude.
    additionalContext reaches Claude as a system reminder next to the tool
    result.
    """
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                "additionalContext": message}},
        stream or sys.stdout,
        ensure_ascii=False,
    )
