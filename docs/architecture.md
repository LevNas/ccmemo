# Architecture & Internals

How ccmemo works under the hood. You don't need any of this to use the skills —
it's here for contributors and anyone curious about the design.

## Subagent Delegation (since v1.8.0)

Both `record-knowledge` and `plan-task` delegate their execution to a Sonnet
subagent. This keeps the main conversation context lean while the subagent handles
file I/O and knowledge graph maintenance.

### Structured input template

The main agent prepares four structured fields before delegating:

| Field | Purpose |
|-------|---------|
| `what` | Factual observation or decision |
| `why` | Reasoning behind recording it |
| `context` | Related issues, branches, files |
| `tags_hint` | Recommended tags (validated by subagent) |

This separation ensures consistent entry quality regardless of how the main agent
phrases its instructions.

### Plan-task operation modes

`plan-task` uses an explicit operation mode to guide the subagent:

| Mode | When |
|------|------|
| `session-start` | New session, post-compaction, resume |
| `create-plan` | Starting a new multi-step plan |
| `update-progress` | Progress update or break signal |
| `revise-plan` | Plan approach needs to change |
| `pause` | Taking a break, session end |
| `complete` | All tasks done, wrap up |

## Context Guard (since v1.1.0)

Prevents knowledge loss during context compaction with a three-stage defense:

| Stage | Event | Role | Can Block? |
|-------|-------|------|------------|
| 1st | PostToolUse | Appends file changes to active task's `context-*.md` | NO (side effect) |
| 2nd | Stop | Prompts `/record-knowledge` when context grows large | YES |
| 3rd | PreCompact | Saves checkpoint of modified files & decisions | NO (side effect) |

**Stage 1 (PostToolUse hook):** Every time Write or Edit modifies a file, the change
is automatically appended to the active task's `context-*.md` file. This provides
incremental context capture that survives compaction. Only fires when an active task
exists in `.claude/tasks/readme.md`.

**Stage 2 (Stop hook):** When the transcript exceeds 300KB and no knowledge entry has
been recorded recently, Claude pauses and asks if you want to run `/record-knowledge`.
Answer "不要" to skip.

**Stage 3 (PreCompact hook):** Before compaction, a checkpoint is automatically saved
to `.claude/context-checkpoints/` with modified file paths and user decisions extracted
from the transcript tail.

### Checkpoint lifecycle

Checkpoints saved by the PreCompact hook are consumed by `/plan-task` on the next
session start or after compaction:

1. Read each checkpoint file in `.claude/context-checkpoints/`
2. Integrate modified file lists and user decisions into the active task's `context-*.md`
3. If a checkpoint contains knowledge-worthy findings, invoke `/record-knowledge`
4. Delete consumed checkpoint files

The `.claude/context-checkpoints/` directory is created on-demand when the first
compaction occurs — it does not exist until then.

### Configuration

Set the size threshold for the Stop hook via environment variable:

```bash
export CCMEMO_CONTEXT_GUARD_THRESHOLD_KB=500  # default: 300
```

### Disabling

Remove or comment out the relevant entry in `hooks/hooks.json`, or delete the
`hooks/` directory.
