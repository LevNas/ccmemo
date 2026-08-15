# Architecture & Internals

How ccmemo works under the hood. You don't need any of this to use the skills —
it's here for contributors and anyone curious about the design.

## Scripts & Skill Wiring

Three scripts under `scripts/` back the search and review skills:

| Script | Runtime | Role | Since |
|--------|---------|------|-------|
| `kb_index.py` | `uv` (fastembed, sqlite-vec) | Build/refresh the per-machine vector index (sha256 incremental, idempotent) | v1.11.0 |
| `kb_search.py` | `uv` (fastembed, sqlite-vec) | Hybrid query: lexical + vector arms, RRF fusion, `see:` 1-hop expansion, frontmatter filters | v1.11.0 |
| `kb_graph.py` | plain `python3` (pure stdlib) | On-demand link graph: `stats` / `neighborhood` / `path` / `lineage` / `link-add` / deterministic `lint` | v1.15.0 |

How the skills reach them:

- **`/recall-knowledge`** runs `kb_search.py` from the *main* agent's Bash
  (subagents run in a sandbox that blocks code execution), falling back to
  ripgrep-only when the index or its dependencies are absent, and pointing at
  `kb_index.py` when advising an index build. Multi-hop recalls query
  `kb_graph.py neighborhood` / `path` first and read only the endpoint
  entries; superseded hits are resolved to the current authority with
  `kb_graph.py lineage`. Details: [hybrid-search.md](hybrid-search.md),
  [link-graph.md](link-graph.md).
- **`/review-knowledge`** has the main agent precompute `kb_graph.py stats` +
  `lint` (deterministic, ~1s, no index needed) and pass the output to the
  review subagent, which spends its effort on judgment work (staleness,
  missing connections, synthesis) instead of re-deriving link facts by hand.
  Falls back to the previous read-everything behaviour when `python3` is
  unavailable.
- The per-prompt `UserPromptSubmit` hook stays **ripgrep-only** (instant,
  model-free injection) — neither the vector index nor the graph CLI is wired
  into it.

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

**Agent worktrees:** Stages 1 and 3 skip capture when the session runs inside a
harness-generated agent isolation worktree (`.claude/worktrees/agent-<hex>` or
`wf_<runId>-<n>`) — captures written there are misattributed and die with the
worktree. Detection matches only the harness naming convention, so user-named
worktrees keep capturing. Set `CCMEMO_CAPTURE_AGENT_WORKTREES=1` to opt out of
the suppression.

### Task mirroring for worktrees (issue-centric mode)

In issue-centric mode `.claude/tasks/` is gitignored, so linked worktrees have
no transport for task files: gitignored files are not checked out into a new
worktree, and files written inside one die with it.

- **Outbound (worktree → main checkout):** a SessionEnd hook
  (`sessionend_tasks_mirror.py`) mirrors `.claude/tasks/` back to the main
  checkout when the session ran inside a linked worktree. Copy-only — nothing
  is deleted or overwritten; byte-identical targets are skipped, and a
  differing existing target gets the copy written alongside as
  `<name>-from-<worktree-basename><ext>`. The hook no-ops in git-tracked mode
  (commits are the transport there), in harness-generated agent worktrees,
  and outside worktrees. Opt-out: `CCMEMO_TASKS_MIRROR=0`.
- **Inbound (main checkout → worktree):** list the directory in the
  repository's `.worktreeinclude` file — Claude Code copies the listed
  gitignored files into worktrees it creates:

  ```
  .claude/tasks/**
  ```

  Caveat: `.worktreeinclude` applies only to worktrees the harness creates,
  not to worktrees made by external scripts with plain `git worktree add`.

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
