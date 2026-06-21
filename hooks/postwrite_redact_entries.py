#!/usr/bin/env python3
"""PostToolUse hook: redact secrets & leak-scan knowledge entries.

Runs after Write/Edit on a knowledge entry (``.claude/knowledge/entries/**.md``)
and enforces the shared redact/leak-scan SPEC deterministically, so record-
knowledge no longer relies on the LLM remembering to sanitize.

Hybrid behaviour (chosen 2026-06-21):
  * Unambiguous secret *values* (op://, JWT, PEM, GitHub token, non-noreply
    email) are masked in place — the file is rewritten with ``‹redacted›``.
  * Leak-prone *shapes* (UUID, home-path, ${...}, base64-ish, private repo
    name) are reported as warnings only — masking them needs human context
    (placeholdering), so we prompt instead of clobbering.

This hook is registered FIRST in the PostToolUse Write|Edit chain so that the
in-place redaction completes before any sibling hook re-reads the file.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import leak_scan, redact  # noqa: E402

ENTRIES_MARKER = (
    os.sep + ".claude" + os.sep + "knowledge" + os.sep + "entries" + os.sep
)


def _is_entry(file_path: str) -> bool:
    if not file_path.endswith(".md"):
        return False
    return ENTRIES_MARKER in os.path.abspath(file_path)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    if input_data.get("tool_name", "") not in ("Write", "Edit"):
        return

    file_path = input_data.get("tool_input", {}).get("file_path", "")
    if not file_path or not _is_entry(file_path) or not os.path.exists(file_path):
        return

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return

    # 1. Redact unambiguous secret values in place.
    redacted, hits = redact.redact_secrets_in_text(content)
    if hits:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(redacted)
        except OSError:
            hits = []  # could not persist — do not claim we redacted
        else:
            content = redacted

    # 2. Leak-scan the (now redacted) content for leak-prone shapes.
    findings = leak_scan.scan(content)

    if not hits and not findings:
        return

    name = os.path.basename(file_path)
    parts: list[str] = []

    if hits:
        summary = ", ".join(f"{kind}×{count}" for kind, count in hits)
        parts.append(
            f"[ccmemo] 秘密値を自動 redact しました ({name}): {summary}\n"
            f"ファイルは ‹redacted› で更新済みです。"
        )

    if findings:
        listed = "\n".join(
            f"  - L{f.lineno} [{f.kind}] {f.snippet} → {f.suggestion}"
            for f in findings
        )
        parts.append(
            f"[ccmemo] leak-scan 警告 ({name}):\n{listed}\n"
            f"上記は自動修正していません。プレースホルダ化や除去を検討してください。"
        )

    result = {"decision": "warn", "reason": "\n\n".join(parts)}
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
