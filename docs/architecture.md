# Architecture & Internals

How ccmemo works under the hood. You don't need any of this to use the skills —
it's here for contributors and anyone curious about the design.

## Scripts & Skill Wiring

Three scripts under `scripts/` back the search and review skills, and one shared
library module keeps their view of an entry's frontmatter identical:

| Script | Runtime | Role | Since |
|--------|---------|------|-------|
| `kb_index.py` | `uv` (fastembed, sqlite-vec) | Build/refresh the per-machine vector index (sha256 incremental, idempotent); since v1.28.0 optionally over every document git knows in the repository (`scope: repo`), each with a `kind`, from the main checkout only | v1.11.0 |
| `kb_search.py` | `uv` (fastembed, sqlite-vec) | Hybrid query: lexical + vector arms, RRF fusion, `see:` 1-hop expansion, frontmatter filters; `--kind` and same-content / same-`id` folding (v1.28.0) | v1.11.0 |
| `kb_graph.py` | plain `python3` (pure stdlib) | On-demand link graph: `stats` / `neighborhood` / `path` / `lineage` / `link-add` / deterministic `lint`; `union-recover` for append-only files diverged across checkouts (v1.23.0); `near-pairs` reads the search index (needs `sqlite_vec`, v1.28.0) | v1.15.0 |
| `hooks/lib/config.py` | plain `python3` (pure stdlib) | `.claude/ccmemo.json` loader: index scope, extensions, include / exclude globs, size cap, `corpora` kind rules — the one place a repository describes its own layout; ccmemo itself knows only the knowledge root | v1.28.0 |
| `hooks/lib/repo.py` | plain `python3` (pure stdlib) | Repository resolution through git: toplevel, main checkout (`--git-common-dir`), linked-worktree detection, `git ls-files` candidate set — shared by the index, the search and the graph CLI so all three agree on where the index lives | v1.28.0 |
| `hooks/lib/frontmatter.py` | plain `python3` (pure stdlib) | The one frontmatter parser every reader imports (both scripts above, `regenerate-tag-registry.py`, the UserPromptSubmit hook via its CLI): YAML subset, normalized view — `tags` always a `#tag` list from either form, missing `status` → `active` | v1.24.0 |

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
  into it. Since v1.19.0 it is status-aware: only `active` entries surface by
  default (`CCMEMO_SEARCH_STATUS` widens or disables the filter, and
  deliberately surfaced non-active entries carry a `[status: …]` annotation),
  still with plain rg/awk and no index.

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

Prevents knowledge loss during context compaction with a three-stage defense,
plus a fourth stage that brings the saved state back afterwards (since v1.29.0):

| Stage | Event | Role | Can Block? |
|-------|-------|------|------------|
| 1st | PostToolUse | Appends file changes to active task's `context-*.md` | NO (side effect) |
| 2nd | Stop | Prompts `/record-knowledge` when context grows large | YES |
| 3rd | PreCompact | Saves checkpoint of modified files & decisions | NO (side effect) |
| 4th | SessionStart (`compact`) | Restores that checkpoint and `session_state.md` into context | NO (adds context) |

**Stage 1 (PostToolUse hook):** Every time Write or Edit modifies a file, the change
is automatically appended to the active task's `context-*.md` file. This provides
incremental context capture that survives compaction. Only fires when an active task
is found (`hooks/lib/tasks.py`: `CCMEMO_ACTIVE_TASK`, a `Branch` match in
`.claude/tasks/readme.md`, or the only active row; otherwise none, so a capture
never lands in an unrelated task).

**Stage 2 (Stop hook):** When the transcript exceeds 300KB and no knowledge entry
has been written recently, the stop is blocked once so the *model* self-assesses:
it either records (via `/record-knowledge` / session-wrap) or ends the session
when only routine work happened. "Recorded recently" is judged by entry-file
mtimes under `.claude/knowledge/entries/` — not by scanning the transcript,
where mere path *mentions* (the per-prompt auto-search injection alone contains
entry paths) used to suppress the nudge almost permanently, and where the
canonical subagent recording flow leaves no Write call at all.

**Stage 3 (PreCompact hook):** Before compaction, a checkpoint is automatically saved
to `.claude/context-checkpoints/` with modified file paths from the transcript tail,
and the user's latest prompts and `AskUserQuestion` answers from the whole transcript.

**Stage 4 (SessionStart hook, matcher `compact`):** PreCompact cannot add
context, and the harness discards its `systemMessage`, so stage 3 alone saves
state the model never sees. SessionStart with source `compact` fires right
after auto or manual compaction and its `additionalContext` does reach the
model. `sessionstart_compact_restore.py` reads back the newest checkpoint whose
`session_id` matches the session, and the active task's `session_state.md`
with its `updated:` time, framed as notes rather than instructions. The
payload stays under 8,000 characters (the harness caps `additionalContext` at
10,000); a section that does not fit ends with the path to read. It is
read-only, so `/plan-task` still consumes the checkpoints as below. Opt out
with `CCMEMO_COMPACT_RESTORE=0`.

**Agent worktrees:** Stages 1 and 3 skip capture (and stage 4 restores nothing) when the session runs inside a
harness-generated agent isolation worktree (`.claude/worktrees/agent-<hex>` or
`wf_<runId>-<n>`) — captures written there are misattributed and die with the
worktree. Detection matches only the harness naming convention, so user-named
worktrees keep capturing. Set `CCMEMO_CAPTURE_AGENT_WORKTREES=1` to opt out of
the suppression.

### Multiple checkouts in git-tracked mode (issue #24)

In git-tracked mode every checkout of a repository — linked worktrees, clones
on other machines — appends to a same-named `context-*.md`, and the copies
diverge: `git pull` then refuses to overwrite local changes, or a merge/rebase
reports an append-vs-append conflict. Nothing is lost (both sides are partial
logs of real activity), and two mitigations exist:

- **Prevention, opt-in — `CCMEMO_CAPTURE_CHECKOUT_SUFFIX=1`.** Stage 1 names
  new capture files `context-<YYYYMMDD-HHMMSS>-session-<id8>.md` and reuses
  only today's unconsumed file ending in its own `<id8>`; files of other
  checkouts are never appended to, so copies no longer share a name. `<id8>`
  is the first 8 hex digits of `sha256(hostname + NUL + realpath(git
  toplevel))` (realpath of the working directory outside git), computed in
  `hooks/lib/checkout_id.py`. It is a one-way digest on purpose: capture files
  are committed, possibly to public repositories, so the hostname and the path
  never appear in a filename, body or log line. Cost: one capture file per
  checkout per day instead of one per day, and one `git rev-parse` per captured
  Write/Edit. Without the variable, naming and reuse are exactly as before.
  Every reader addresses captures as `context-*.md`, so suffixed names need no
  other configuration; enable it in every checkout that should stop sharing
  files (a checkout without it keeps the legacy rule and appends to any of
  today's files, suffixed or not).
- **Recovery — `kb_graph.py union-recover <file>`.** Automates the lossless
  union of two append-only copies, for captures and for hub-entry `see:`
  blocks alike, and refuses anything that is not append-only. See
  [link-graph.md](link-graph.md#union-recover-file---theirs-ref).

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

Within the session that compacted, stage 4 has already put the newest
checkpoint back into context; `/plan-task` remains the step that merges
checkpoints into the task record and deletes them.

The `.claude/context-checkpoints/` directory is created on-demand when the first
compaction occurs — it does not exist until then.

### Configuration

Two environment variables tune the Stop hook:

```bash
export CCMEMO_CONTEXT_GUARD_THRESHOLD_KB=500      # default: 300
export CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN=45   # default: 45
```

- `…_THRESHOLD_KB` — transcript size at which the nudge starts firing. The
  default 300KB corresponds very roughly to a few tens of thousands of context
  tokens; sessions on a 200k-token window that want a single mid-session nudge
  comfortably before auto-compaction typically raise it (800–1200KB observed
  to work well — transcript bytes run at roughly 10KB per 1k context tokens,
  tool-output-heavy sessions higher).
- `…_RECENT_WRITE_MIN` — how long one entry write, or one nudge, keeps the
  nudge quiet, so a session that just recorded is not immediately re-nudged,
  and a long session whose transcript stays above the threshold is nudged at
  most once per window instead of at every turn. Each nudge leaves an empty
  per-session marker in `${XDG_CACHE_HOME:-~/.cache}/ccmemo/context-guard/`
  (outside the repository), named by a hash of the session id; its mtime is
  the nudge time. Markers older than seven days are pruned.

### Disabling

Remove or comment out the relevant entry in `hooks/hooks.json`, or delete the
`hooks/` directory.

## Entry Redaction & Leak Scan

`postwrite_redact_entries.py` runs after every Write/Edit on a knowledge entry
(`.claude/knowledge/entries/**.md`) and enforces a shared redact/leak-scan SPEC
deterministically, so recording does not rely on the model remembering to
sanitize. Hybrid behaviour (chosen 2026-06-21):

- **Unambiguous secret values** — `op://` references (1Password
  secret-reference URIs), JWTs, PEM private-key headers, GitHub tokens,
  non-noreply emails — are masked in place with `‹redacted›`.
- **Leak-prone shapes** — UUIDs, home paths, `${…}`, base64-ish strings,
  private repo names (`CCMEMO_PRIVATE_REPO_NAMES`) — are reported as warnings
  only; masking them correctly needs human context (placeholdering), so the
  hook prompts instead of clobbering. Since 1.30.2 these warnings reach Claude
  only with `CCMEMO_LEAK_SCAN_WARN=1`: on ordinary entries the scan flags the
  frontmatter `id`, the repository's own name and links between entries
  (LevNas/ccmemo#63). The redaction notice is always delivered.

The pattern set lives in `hooks/lib/redact.py` and mirrors a TypeScript
counterpart — the two implementations share the SPEC, not the code. One
deliberate deviation: the `op://` pattern matches only the URI charset and
must end on an alphanumeric, so it cannot swallow a closing quote, backtick
or paren adjacent to a reference (CHANGELOG 1.21.0/1.21.1 has the full
rationale, including the accepted non-canonical-tail differential).

`CCMEMO_REDACT_OP_REF=keep-names` narrows the `op://` masking to references
containing a raw 26-character item/vault ID segment, keeping item-name
references, which carry no secret value. Default: every reference is masked.
The same `redact`/`leak_scan` modules also gate the opt-in auto-commit
(`CCMEMO_AUTOCOMMIT`): a safety-net commit is blocked while the staged diff
contains leak-prone shapes.
