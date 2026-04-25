# ccmemo

Claude Code starts every session with a blank slate. The quirk you debugged yesterday, the plan you were halfway through — all gone when the session ends. ccmemo saves that knowledge and progress as Markdown files in your repository, so the next session picks up where you left off.

## What It Does

- **Knowledge Base** (`/record-knowledge`): Captures tacit knowledge — quirks, pitfalls, decisions — as tagged Markdown entries in `.claude/knowledge/entries/`
- **Plan & Task Persistence** (`/plan-task`): Maintains multi-step plans and task progress across sessions in `.claude/tasks/`
- **Knowledge Review** (`/review-knowledge`): Maintains knowledge base health — finds stale entries, orphan entries, missing connections, and tag issues. Supports periodic review and internalization of accumulated knowledge

## Install

### Option 1: Plugin (recommended)

```bash
# Add the marketplace
/plugin marketplace add LevNas/claudecode-plugins

# Install ccmemo
/plugin install ccmemo@levnas-plugins
```

Then copy the templates into your project:

```bash
# From your project root
cp -r path/to/ccmemo/templates/knowledge .claude/knowledge
cp -r path/to/ccmemo/templates/tasks .claude/tasks
```

### Option 2: Manual copy

Copy the skills and templates you need into your project:

```bash
# From your project root

# Knowledge base (record-knowledge)
cp -r path/to/ccmemo/skills/record-knowledge .claude/skills/record-knowledge
cp -r path/to/ccmemo/templates/knowledge .claude/knowledge

# Plan & task persistence (plan-task)
cp -r path/to/ccmemo/skills/plan-task .claude/skills/plan-task
cp -r path/to/ccmemo/templates/tasks .claude/tasks
```

Your project should now have:

```
your-project/
└── .claude/
    ├── skills/
    │   ├── record-knowledge/
    │   │   └── SKILL.md
    │   ├── plan-task/
    │   │   └── SKILL.md
    │   └── review-knowledge/
    │       └── SKILL.md
    ├── knowledge/
    │   ├── CLAUDE.md
    │   └── entries/
    │       └── .gitkeep
    └── tasks/
        ├── CLAUDE.md
        └── readme.md
```

## Usage

### Recording Knowledge

Claude Code automatically uses `/record-knowledge` when it discovers something worth noting. You can also trigger it explicitly:

```
/record-knowledge
```

Claude Code will create an entry like `.claude/knowledge/entries/20260302-143052-alice-docker-compose-port-conflict.md`:

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

### Searching Entries

```bash
# Fuzzy search by filename
fd -e md . .claude/knowledge/entries/ | fzf

# Search by tag
rg '#pitfall' .claude/knowledge/entries/

# List all titles
rg '^title:' .claude/knowledge/entries/

# Active entries only
rg '^status: active' .claude/knowledge/entries/
```

### Reviewing Knowledge

Periodically run `/review-knowledge` to maintain knowledge base health:

```
/review-knowledge
```

Three modes are available:

- **Health Check** (default): Scans the entire knowledge base and reports stale entries (>90 days), orphan entries (no `see:` links), missing connections (shared tags but no links), tag issues (unregistered/unused/near-duplicate), and summary statistics
- **Topic Review** (`topic:<keyword>`): Deep dive into a specific topic — summarizes related entries and asks reflective questions to help you verify accuracy
- **Fix Mode** (`fix`): Interactively fixes issues found in the health check — adds missing `see:` links, registers unregistered tags, and reports actions taken

### Plan & Task Management

Claude Code uses `/plan-task` to persist multi-step plans across sessions. Two modes are available:

#### Git-tracked mode (default)

`.claude/tasks/` is committed to Git and serves as the shared source of truth:

1. **Create**: Start a plan with `plan-v1.md`, `todo.md`, and `readme.md` in `.claude/tasks/<slug>-<account>-<date>/`
2. **Work**: Update `todo.md` as tasks progress (`[ ]` → `[~]` → `[x]`)
3. **Revise**: If the plan changes, create `plan-v2.md` (previous versions are preserved)
4. **Pause/Resume**: On session end, update the plan's `readme.md` with handoff notes. Next session checks `.claude/tasks/readme.md` for incomplete plans
5. **Complete**: Mark done in both the plan directory and the task index

If your project uses an issue tracker, plans can optionally link to issues for bidirectional progress tracking.

Each task directory also contains **context-\*.md** files that capture detailed working context (investigation results, trial & error, decision rationale) too granular for plan files but essential for resuming work. These are written incrementally — both automatically by the PostToolUse hook (file changes) and manually for reasoning and analysis. See [Context Guard](#context-guard-v110) for details.

#### Issue-centric mode

For teams already using an issue tracker (GitLab, GitHub Issues, Jira, etc.) as the primary source of truth:

1. **Gitignore tasks**: Add `.claude/tasks/` to `.gitignore`
2. **Use issue tracker**: Plans, progress, and decisions live in the issue tracker
3. **Local scratchpad**: `.claude/tasks/` becomes a local working memo for the current session
4. **Session start**: Check assigned issues (e.g., `glab issue list --assignee=@me`) instead of `.claude/tasks/readme.md`

### Using Knowledge in CLAUDE.md

Add a lookup section to your project's `CLAUDE.md` so Claude Code checks relevant entries before starting work:

```markdown
## Knowledge Base Lookup

Before starting work, search for relevant active knowledge entries:

\```bash
rg '^status: active' .claude/knowledge/entries/ -l | xargs rg '<keyword>' -l
\```
```

For more detailed CLAUDE.md configuration examples, see [docs/claude-md-examples.md](docs/claude-md-examples.md).

## Real-World Examples

### Personal: Capturing knowledge from debugging sessions

While debugging a CI pipeline failure, Claude Code discovers that a dependency upgrade changed its config file format. The fix is simple, but without recording the context, the next session has no idea why the config looks the way it does.

With `/record-knowledge`, Claude Code saves the discovery:

```markdown
---
title: webpack 6 requires updated config format
author: "@alice"
created: 2026-03-08
status: active
tags: "#webpack #migration #pitfall"
---

webpack 6 dropped support for `module.rules[].loader` shorthand.
Must use `module.rules[].use` array format instead.

The CI failure after the upgrade was caused by this breaking change.
- ref: https://webpack.js.org/migrate/6/
- see: [Node.js 22 upgrade notes](nodejs-22-upgrade.md)
```

The next session — or a teammate's session — finds this entry automatically and avoids repeating the same investigation.

### Team: Syncing progress through issue tracker

A team manages their project roadmap in GitHub Issues. Each member uses Claude Code in their own session, but progress needs to stay visible to everyone.

With `/plan-task` in issue-centric mode, Claude Code checks assigned issues at session start (`gh issue list --assignee=@me`) and posts progress comments when work is done. The issue's checklist is updated directly — no manual copy-paste between sessions.

```markdown
## Progress Update (2026-03-08)

### Completed
- Migrated database schema to v3
- Added index on `users.email` column

### Next Steps
- Update API validation to match new schema constraints
```

Every team member sees the latest state in the issue tracker, regardless of who worked on it or which Claude Code session produced the update.

## Why Use It in a Git Repository

Both skills store data as plain Markdown files in your repository. This means they follow the same branch/merge/review workflow as your code, and bring additional benefits:

- **Team sharing**: Knowledge entries and plans committed to Git are available to every team member — and every Claude Code session they start. A pitfall one person discovers on Monday is available to the entire team on Tuesday.
- **Session continuity**: Plans and knowledge survive across Claude Code sessions. No more re-explaining context or re-discovering the same issues.
- **Browsable**: All files render cleanly in GitHub and GitLab's web UI — no special tooling needed.

## Customization

### Tags (Knowledge Base)

Edit `.claude/knowledge/CLAUDE.md` to maintain a tag registry for your project. Tags use lowercase kebab-case with `#` prefix as a flat list:

```
`#docker` `#postgresql` `#react` `#pitfall` `#workaround` `#design-practice`
```

Claude Code checks the registry before creating new tags to avoid duplicates.

### Entry Author (Knowledge Base)

The `author` field in entries defaults to `@<username>`. Set this to match your Git hosting platform username.

### Issue Tracker Integration (Plan & Task)

Plans can optionally link to issues. Edit `skills/plan-task/SKILL.md` to add project-specific conventions — for example, specifying your issue tracker's comment format or commit message conventions.

### Integration with Project Workflow

You can extend either skill by editing its `SKILL.md` to fit your workflow — for example, adding project-specific tag categories, linking to your issue tracker, or customizing plan templates.

## Subagent Delegation (v1.8.0)

Both `record-knowledge` and `plan-task` skills delegate their execution to a Sonnet subagent. This keeps the main conversation context lean while the subagent handles file I/O and knowledge graph maintenance.

### Structured Input Template

The main agent prepares four structured fields before delegating:

| Field | Purpose |
|-------|---------|
| `what` | Factual observation or decision |
| `why` | Reasoning behind recording it |
| `context` | Related issues, branches, files |
| `tags_hint` | Recommended tags (validated by subagent) |

This separation ensures consistent entry quality regardless of how the main agent phrases its instructions.

### Plan-Task Operation Modes

`plan-task` uses an explicit operation mode to guide the subagent:

| Mode | When |
|------|------|
| `session-start` | New session, post-compaction, resume |
| `create-plan` | Starting a new multi-step plan |
| `update-progress` | Progress update or break signal |
| `revise-plan` | Plan approach needs to change |
| `pause` | Taking a break, session end |
| `complete` | All tasks done, wrap up |

## Context Guard (v1.1.0)

Prevents knowledge loss during context compaction with a three-stage defense:

### How It Works

| Stage | Event | Role | Can Block? |
|-------|-------|------|------------|
| 1st | PostToolUse | Appends file changes to active task's `context-*.md` | NO (side effect) |
| 2nd | Stop | Prompts `/record-knowledge` when context grows large | YES |
| 3rd | PreCompact | Saves checkpoint of modified files & decisions | NO (side effect) |

**Stage 1 (PostToolUse hook):** Every time Write or Edit tools modify a file, the change is automatically appended to the active task's `context-*.md` file. This provides incremental context capture that survives context compaction. Only fires when an active task exists in `.claude/tasks/readme.md`.

**Stage 2 (Stop hook):** When the transcript exceeds 300KB and no knowledge entry has been recorded recently, Claude pauses and asks if you want to run `/record-knowledge`. Answer "不要" to skip.

**Stage 3 (PreCompact hook):** Before compaction, a checkpoint is automatically saved to `.claude/context-checkpoints/` with modified file paths and user decisions extracted from the transcript tail.

### Checkpoint Lifecycle

Checkpoints saved by the PreCompact hook are consumed by `/plan-task` on the next session start or after compaction:

1. Read each checkpoint file in `.claude/context-checkpoints/`
2. Integrate modified file lists and user decisions into the active task's `context-*.md`
3. If a checkpoint contains knowledge-worthy findings, invoke `/record-knowledge`
4. Delete consumed checkpoint files

The `.claude/context-checkpoints/` directory is created on-demand when the first compaction occurs — it does not exist until then.

### Configuration

Set the size threshold for the Stop hook via environment variable:

```bash
export CCMEMO_CONTEXT_GUARD_THRESHOLD_KB=500  # default: 300
```

### Disabling

Remove or comment out the relevant entry in `hooks/hooks.json`, or delete the `hooks/` directory.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project's scope, plugin conventions (document placement, SKILL.md frontmatter, the central linter command), pull request guidelines, and attribution requests for forks.

## License

This project is licensed under the [MIT License](LICENSE) — see the `LICENSE` file for the full text.

In short: you're free to use, modify, and redistribute ccmemo, including for commercial purposes, as long as you keep the copyright notice and license text.

If you build something based on ccmemo, a credit line is appreciated (not required):

> Based on [ccmemo](https://github.com/LevNas/ccmemo) by LevNas.
