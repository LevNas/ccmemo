# ccmemo

[![lint](https://github.com/LevNas/ccmemo/actions/workflows/lint.yml/badge.svg)](https://github.com/LevNas/ccmemo/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-7c5cff)

Claude Code starts every session with a blank slate — ccmemo saves knowledge and
plans as plain Markdown in your repository, so the next session (or a teammate's)
picks up where you left off.

- **`/record-knowledge`** — save quirks, pitfalls, and decisions as tagged Markdown
  entries under `.claude/knowledge/entries/`
- **`/recall-knowledge`** — hybrid semantic search over entries (ripgrep + local
  vector embeddings + the `see:`-link graph); falls back to plain ripgrep when the
  index is absent
- **`/review-knowledge`** — knowledge-base health checks: stale entries, orphans,
  missing links, tag issues
- **`/plan-task`** — persist multi-step plans and progress across sessions,
  Git-tracked or issue-centric
- **Link-graph CLI** — `scripts/kb_graph.py` answers structural questions (hubs,
  neighborhoods, shortest link paths) and lints link integrity; pure stdlib,
  pre-commit friendly
- **Context Guard** — hooks that defend against knowledge loss when the context
  is compacted
- **Plain Markdown in git** — entries follow the same branch/merge/review workflow
  as your code and render cleanly on GitHub/GitLab

Claude Code invokes the skills automatically when relevant; you can also trigger
any of them by name.

## Quick start

**1. Install the plugin** (in Claude Code):

```
/plugin marketplace add LevNas/claudecode-plugins
/plugin install ccmemo@levnas-plugins
```

That alone makes the four skills available and wires up the
[Context Guard](docs/architecture.md#context-guard-since-v110) hooks.

**2. Scaffold the starter config** — a tag registry for knowledge and an index
for tasks. Easiest is to just ask Claude Code:

> Scaffold ccmemo's knowledge and tasks templates into `.claude/`.

**3. Try it.** Run `/record-knowledge` and describe something worth remembering —
Claude writes a Markdown entry under `.claude/knowledge/entries/`. Commit it, and
your next session (or a teammate's) finds it automatically.

A step-by-step version of this Quick start — repository layout, user-scope
install, team enablement — is [docs/getting-started.md](docs/getting-started.md).
Installing without the marketplace and scaffolding by shell command are covered
in [docs/usage.md](docs/usage.md). Semantic search via `/recall-knowledge` is
opt-in (one-time index build) — see [docs/hybrid-search.md](docs/hybrid-search.md).

## Examples

### A recorded entry

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

Personal and team workflow walkthroughs: [docs/examples.md](docs/examples.md).

### Querying the link graph

Multi-hop questions ("how did this decision evolve", "how do X and Y connect")
go structure-first through the `see:`-link graph — pure stdlib, no index:

```bash
python3 scripts/kb_graph.py stats                    # hubs, orphans, components
python3 scripts/kb_graph.py path <entry-a> <entry-b> # shortest link path
python3 scripts/kb_graph.py lineage <entry>          # supersede chain → current authority
```

All subcommands and the pre-commit lint: [docs/link-graph.md](docs/link-graph.md).

## Documentation

- [docs/getting-started.md](docs/getting-started.md) — zero-to-first-recall
  walkthrough: repository layout (ghq), user-scope install, scaffolding, and
  scaling up with ccorch ([日本語](docs/getting-started.ja.md))
- [docs/usage.md](docs/usage.md) — day-to-day usage: searching entries, reviewing
  the base, plans & tasks, customization, manual install
  ([日本語](docs/usage.ja.md))
- [docs/hybrid-search.md](docs/hybrid-search.md) — semantic search setup &
  verification (incl. corporate TLS and NixOS)
- [docs/link-graph.md](docs/link-graph.md) — the link-graph CLI:
  `stats` / `neighborhood` / `path` / `lineage` / `link-add` / `lint`
- [docs/architecture.md](docs/architecture.md) — scripts & skill wiring, subagent
  delegation, Context Guard
- [docs/examples.md](docs/examples.md) — personal & team workflow walkthroughs
- [docs/claude-md-examples.md](docs/claude-md-examples.md) — CLAUDE.md integration
  patterns ([日本語](docs/claude-md-examples.ja.md))

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
