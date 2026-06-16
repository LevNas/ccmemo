# ccmemo

[![lint](https://github.com/LevNas/ccmemo/actions/workflows/lint.yml/badge.svg)](https://github.com/LevNas/ccmemo/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-7c5cff)

Claude Code starts every session with a blank slate. The quirk you debugged yesterday,
the plan you were halfway through — all gone when the session ends. ccmemo saves that
knowledge and progress as Markdown files in your repository, so the next session picks
up where you left off.

## What It Does

ccmemo adds four slash commands that persist knowledge and plans as plain Markdown in
`.claude/`:

| Command | What it does | More |
|---------|--------------|------|
| `/record-knowledge` | Save a quirk, pitfall, or decision as a tagged entry | [Example](#example) |
| `/recall-knowledge` | Hybrid semantic search across entries (synonyms, cross-language) | [docs/hybrid-search.md](docs/hybrid-search.md) |
| `/review-knowledge` | Keep the knowledge base healthy — stale/orphan entries, missing links, tag issues | [Reviewing](#reviewing-knowledge) |
| `/plan-task` | Persist multi-step plans and progress across sessions | [Plan & task](#plan--task-management) |

Claude Code invokes these automatically when relevant; you can also trigger any of them
by name.

## Quick start

Up and running in three steps. The plugin is the recommended path — skills and hooks
activate the moment it's installed.

**1. Install the plugin** (in Claude Code):

```
/plugin marketplace add LevNas/claudecode-plugins
/plugin install ccmemo@levnas-plugins
```

That alone makes `/record-knowledge`, `/plan-task`, `/review-knowledge`, and
`/recall-knowledge` available and wires up the [Context Guard](docs/architecture.md#context-guard-since-v110) hooks.

**2. Scaffold the starter config** into your project — a tag registry for knowledge and
an index for tasks. Easiest is to just ask Claude Code:

> Scaffold ccmemo's knowledge and tasks templates into `.claude/`.

Prefer a command? From your project root:

```bash
tpl=$(find ~/.claude/plugins/cache -type d -path '*ccmemo*/templates' | sort | tail -1)
cp -r "$tpl/knowledge" .claude/knowledge
cp -r "$tpl/tasks"     .claude/tasks
```

**3. Try it.** Run `/record-knowledge` and describe something worth remembering — Claude
writes a Markdown entry under `.claude/knowledge/entries/`. Commit it, and your next
session (or a teammate's) finds it automatically.

> Semantic search via `/recall-knowledge` is opt-in (one-time index build) — see
> [docs/hybrid-search.md](docs/hybrid-search.md).

### Without the marketplace

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

## Example

`/record-knowledge` creates an entry like
`.claude/knowledge/entries/20260302-143052-alice-docker-compose-port-conflict.md`:

```markdown
---
title: Docker Compose port conflict with host nginx
author: "@alice"
created: 2026-03-02
status: active
tags: "#docker #nginx #pitfall"
---

When running `docker compose up`, port 80 conflicts with the host's nginx.
Use `ports: ["8080:80"]` or stop host nginx first.

- ref: [Docker Compose networking](https://docs.docker.com/compose/networking/)
- see: [Nginx reverse proxy setup](nginx-reverse-proxy.md) — related configuration
```

See [docs/examples.md](docs/examples.md) for personal and team workflow walkthroughs.

## Usage

### Searching entries

Quick keyword/filename lookups need no setup:

```bash
fd -e md . .claude/knowledge/entries/ | fzf   # fuzzy search by filename
rg '#pitfall' .claude/knowledge/entries/      # search by tag
rg '^title:' .claude/knowledge/entries/       # list all titles
```

For meaning-based search (synonyms, or a Japanese query against English identifiers),
`/recall-knowledge` runs hybrid semantic search — ripgrep + local vector embeddings +
the `see:`-link graph — and falls back to ripgrep when the index is absent. Setup:
[docs/hybrid-search.md](docs/hybrid-search.md).

### Reviewing knowledge

`/review-knowledge` keeps the base healthy in three modes:

- **Health check** (default) — reports stale entries (>90 days), orphans (no `see:`
  links), missing connections, tag issues, and summary stats
- **Topic review** (`topic:<keyword>`) — summarizes a topic and asks reflective
  questions to verify accuracy
- **Fix mode** (`fix`) — interactively adds missing links and registers tags

### Plan & task management

`/plan-task` persists multi-step plans in `.claude/tasks/`. Two modes:

- **Git-tracked** (default) — plans (`plan-v1.md`, `todo.md`, `readme.md`) are committed
  and become the shared source of truth. Progress moves `[ ]` → `[~]` → `[x]`; plan
  revisions are kept as `plan-v2.md`, etc. Each task dir also holds `context-*.md` files
  that capture detailed working context (see [Context Guard](docs/architecture.md#context-guard-since-v110)).
- **Issue-centric** — gitignore `.claude/tasks/` and treat your issue tracker (GitHub,
  GitLab, Jira) as the source of truth; `.claude/tasks/` becomes a per-session
  scratchpad. Session start checks assigned issues instead of `readme.md`.

### Wiring into CLAUDE.md

Add a lookup section to your project's `CLAUDE.md` so Claude checks relevant entries
before starting work. Patterns and examples: [docs/claude-md-examples.md](docs/claude-md-examples.md).

## Customization

- **Tags** — maintain a registry in `.claude/knowledge/CLAUDE.md` (lowercase
  kebab-case, `#` prefix). Claude checks it before creating new tags.
- **Author** — the entry `author` field defaults to `@<username>`; set it to your Git
  hosting username.
- **Workflow** — edit any `skills/*/SKILL.md` to add project-specific conventions
  (tag categories, issue-tracker comment formats, plan templates).

## Why Use It in a Git Repository

Everything is plain Markdown in your repo, so it follows the same branch/merge/review
workflow as your code:

- **Team sharing** — a pitfall one person finds on Monday is available to everyone
  (and every Claude Code session) on Tuesday.
- **Session continuity** — plans and knowledge survive across sessions; no
  re-explaining context or re-discovering the same issues.
- **Browsable** — files render cleanly in GitHub and GitLab with no special tooling.

## Documentation

- [docs/architecture.md](docs/architecture.md) — subagent delegation, Context Guard, internals
- [docs/examples.md](docs/examples.md) — personal & team workflow walkthroughs
- [docs/hybrid-search.md](docs/hybrid-search.md) — semantic search setup & verification (incl. corporate TLS)
- [docs/claude-md-examples.md](docs/claude-md-examples.md) — CLAUDE.md configuration

## Getting help

- **Usage or design questions** — open a [Question issue](https://github.com/LevNas/ccmemo/issues/new?template=question.md)
- **Bugs & feature requests** — pick a template from [new issue](https://github.com/LevNas/ccmemo/issues/new/choose)
- **What changed** — see the [CHANGELOG](CHANGELOG.md)

## Contributing

Maintained by [@LevNas](https://github.com/LevNas); contributions and forks welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md) for scope, plugin conventions, the linter
command, and pull request guidelines.

## License

[MIT](LICENSE). If you build on ccmemo, a credit line is appreciated (not required):

> Based on [ccmemo](https://github.com/LevNas/ccmemo) by LevNas.
