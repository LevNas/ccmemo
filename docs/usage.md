# Usage Guide

> 日本語版: [usage.ja.md](usage.ja.md)

Day-to-day workflows once ccmemo is installed, plus manual installation and
customization. For the fastest install path, see the Quick start in the
[README](../README.md).

## Installing without the marketplace

Copy the skills and templates straight from the repo:

```bash
git clone --depth 1 https://github.com/LevNas/ccmemo /tmp/ccmemo
cp -r /tmp/ccmemo/skills/*            .claude/skills/      # the four skills
cp -r /tmp/ccmemo/templates/knowledge .claude/knowledge
cp -r /tmp/ccmemo/templates/tasks     .claude/tasks
rm -rf /tmp/ccmemo
```

Your project ends up with:

```
your-project/.claude/
├── skills/{record-knowledge,plan-task,review-knowledge,recall-knowledge}/SKILL.md
├── knowledge/{CLAUDE.md,entries/}
└── tasks/{CLAUDE.md,readme.md}
```

Hooks (Context Guard) are wired through the plugin; with a manual copy, add the
`hooks/hooks.json` entries yourself if you want them.

### Scaffolding templates by hand (plugin install)

If you installed via the marketplace and prefer a shell command over asking
Claude Code to scaffold, run from your project root:

```bash
tpl=$(find ~/.claude/plugins/cache -type d -path '*ccmemo*/templates' | sort | tail -1)
cp -r "$tpl/knowledge" .claude/knowledge
cp -r "$tpl/tasks"     .claude/tasks
```

## Searching entries

Quick keyword/filename lookups need no setup:

```bash
fd -e md . .claude/knowledge/entries/ | fzf   # fuzzy search by filename
rg '#pitfall' .claude/knowledge/entries/      # search by tag
rg '^title:' .claude/knowledge/entries/       # list all titles
```

For meaning-based search (synonyms, or a Japanese query against English
identifiers), `/recall-knowledge` runs hybrid semantic search — ripgrep +
local vector embeddings + the `see:`-link graph — and falls back to ripgrep
when the index is absent. Setup: [hybrid-search.md](hybrid-search.md).

For multi-hop questions ("how did this decision evolve", "how do X and Y
connect"), `scripts/kb_graph.py` queries the `see:`-link graph directly —
pure stdlib, no index needed. The `lineage` subcommand follows the supersede
chain from any entry to the one that currently holds the answer. Subcommands
and pre-commit lint: [link-graph.md](link-graph.md).

## Reviewing knowledge

`/review-knowledge` keeps the base healthy in three modes:

- **Health check** (default) — reports stale entries (>90 days), orphans (no
  `see:` links), missing connections, tag issues, and summary stats
- **Topic review** (`topic:<keyword>`) — summarizes a topic and asks
  reflective questions to verify accuracy
- **Fix mode** (`fix`) — interactively adds missing links and registers tags

## Plan & task management

`/plan-task` persists multi-step plans in `.claude/tasks/`. Two modes:

- **Git-tracked** (default) — plans (`plan-v1.md`, `todo.md`, `readme.md`) are
  committed and become the shared source of truth. Progress moves
  `[ ]` → `[~]` → `[x]`; plan revisions are kept as `plan-v2.md`, etc. Each
  task dir also holds `context-*.md` files that capture detailed working
  context (see [Context Guard](architecture.md#context-guard-since-v110)).
- **Issue-centric** — gitignore `.claude/tasks/` and treat your issue tracker
  (GitHub, GitLab, Jira) as the source of truth; `.claude/tasks/` becomes a
  per-session scratchpad. Session start checks assigned issues instead of
  `readme.md`.

## Wiring into CLAUDE.md

Add a lookup section to your project's `CLAUDE.md` so Claude checks relevant
entries before starting work. Patterns and examples:
[claude-md-examples.md](claude-md-examples.md).

## Customization

- **Tags** — maintain a registry in `.claude/knowledge/CLAUDE.md` (lowercase
  kebab-case, `#` prefix). Claude checks it before creating new tags.
- **Author** — the entry `author` field defaults to `@<username>`; set it to
  your Git hosting username.
- **Workflow** — edit any `skills/*/SKILL.md` to add project-specific
  conventions (tag categories, issue-tracker comment formats, plan templates).
- **Auto-commit (opt-in, off by default)** — set `CCMEMO_AUTOCOMMIT=1` and a
  safety-net hook commits *only* `.claude/knowledge/` and `.claude/tasks/`
  changes at session end (`SessionEnd`) and before compaction (`PreCompact`).
  It never runs `git add -A`, **never pushes**, and a leak-scan gate blocks
  commits containing leak-prone shapes (set `CCMEMO_AUTOCOMMIT_ON_LEAK=warn`
  to commit with a warning instead). It complements — does not replace — your
  manual end-of-session commit, so it is a no-op when you have already
  committed.
- **Redact hook `op://` handling** — the entry redact hook masks every
  `op://` reference by default. Set `CCMEMO_REDACT_OP_REF=keep-names` to keep
  item-name references (`op://vault/item-name/field` — no secret value) and
  mask only references containing a raw 26-character item/vault ID segment.
- **Auto-search status filter** — the per-prompt `UserPromptSubmit` hook only
  injects entries whose frontmatter `status` is `active` (entries without a
  `status:` line count as active). Set `CCMEMO_SEARCH_STATUS` to a
  comma-separated allowlist (e.g. `active,superseded`) to widen it, or to
  `all` to disable filtering; non-active entries surfaced that way are
  annotated with `[status: …, superseded_by: …]` so they cannot be mistaken
  for current knowledge.

## Why keep it in a Git repository

Everything is plain Markdown in your repo, so it follows the same
branch/merge/review workflow as your code:

- **Team sharing** — a pitfall one person finds on Monday is available to
  everyone (and every Claude Code session) on Tuesday.
- **Session continuity** — plans and knowledge survive across sessions; no
  re-explaining context or re-discovering the same issues.
- **Browsable** — files render cleanly in GitHub and GitLab with no special
  tooling.
