#!/usr/bin/env python3
"""SessionEnd hook: opt-in safety-net auto-commit of knowledge/tasks.

A thin entry point — all gating and commit logic lives in lib.autocommit so the
PreCompact hook can share it. SessionEnd cannot block termination and its
systemMessage is discarded by Claude Code, so the only channel back to the user
is stderr + a non-zero exit; we use it solely when something needs attention
(leak-scan block, or git error). A successful commit is silent by design.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import autocommit  # noqa: E402


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        data = {}

    cwd = data.get("cwd") or os.getcwd()
    result = autocommit.run(cwd, "SessionEnd")

    if result.status == "blocked":
        sys.stderr.write(autocommit.format_findings(result))
        return 2
    if result.status == "error":
        sys.stderr.write(f"ccmemo autocommit error: {result.reason}\n")
        return 2
    if result.status == "committed" and result.findings:
        # warn mode: committed despite findings — still surface them, exit 0.
        sys.stderr.write(autocommit.format_findings(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
