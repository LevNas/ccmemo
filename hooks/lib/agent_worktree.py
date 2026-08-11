"""Detection of harness-generated agent isolation worktrees (issue #17).

Claude Code runs subagents in ephemeral git worktrees under
``.claude/worktrees/`` with deterministic basenames:

- Agent tool isolation:  ``agent-<hex agentId>``
- Workflow isolation:    ``wf_<runId>-<n>``

SECI captures written inside those worktrees are misattributed to
whatever task is active in the checked-out state and die with the
worktree, so capture hooks skip them (default-on, no configuration).

Detection is deliberately tight and fails toward capturing: only a
basename matching the harness naming convention, directly under a
``.claude/worktrees/`` path component, suppresses capture. A user-named
worktree such as ``agent-foo`` keeps capturing, and if the harness ever
renames its convention the patterns simply stop matching — behavior
falls back to today's (no regression).
"""

import os
import re

_AGENT_RE = re.compile(r"^agent-[0-9a-f]{16,}$")
_WORKFLOW_RE = re.compile(r"^wf_[A-Za-z0-9-]+-[0-9]+$")


def is_agent_worktree(path: str) -> bool:
    """Return True when *path* lies inside a harness-generated agent worktree."""
    if not path:
        return False
    parts = os.path.abspath(path).split(os.sep)
    for i in range(len(parts) - 2):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            basename = parts[i + 2]
            if _AGENT_RE.match(basename) or _WORKFLOW_RE.match(basename):
                return True
    return False


def capture_suppressed(path: str) -> bool:
    """Return True when SECI capture should be skipped for this session.

    Opt-out: ``CCMEMO_CAPTURE_AGENT_WORKTREES=1`` restores capture
    everywhere, for users who do want captures from agent sessions.
    """
    if os.environ.get("CCMEMO_CAPTURE_AGENT_WORKTREES") == "1":
        return False
    return is_agent_worktree(path)
