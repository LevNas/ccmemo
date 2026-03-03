# ccmemo

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill and template collection for persistent knowledge and plan management across sessions.

## What It Does

- **Knowledge Base** (`/record-knowledge`): Captures tacit knowledge — quirks, pitfalls, decisions — as tagged Markdown entries in `.claude/knowledge/entries/`
- **Plan & Task Persistence** (`/plan-task`): Maintains multi-step plans and task progress across sessions in `.claude/tasks/`

## Install

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
    │   └── plan-task/
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

### Plan & Task Management

Claude Code uses `/plan-task` to persist multi-step plans across sessions. The workflow:

1. **Create**: Start a plan with `plan-v1.md`, `todo.md`, and `readme.md` in `.claude/tasks/<slug>-<account>-<date>/`
2. **Work**: Update `todo.md` as tasks progress (`[ ]` → `[~]` → `[x]`)
3. **Revise**: If the plan changes, create `plan-v2.md` (previous versions are preserved)
4. **Pause/Resume**: On session end, update the plan's `readme.md` with handoff notes. Next session checks `.claude/tasks/readme.md` for incomplete plans
5. **Complete**: Mark done in both the plan directory and the task index

If your project uses an issue tracker, plans can optionally link to issues for bidirectional progress tracking.

### Using Knowledge in CLAUDE.md

Add a lookup section to your project's `.claude/CLAUDE.md` so Claude Code checks relevant entries before starting work:

```markdown
## Knowledge Base Lookup

Before starting work, search for relevant active knowledge entries:

\```bash
rg '^status: active' .claude/knowledge/entries/ -l | xargs rg '<keyword>' -l
\```
```

## Example: CLAUDE.md Configuration

Below is a practical example of how to configure your project's `.claude/CLAUDE.md` to integrate the knowledge base into Claude Code's workflow. This setup ensures Claude Code automatically checks relevant entries before starting work and records new discoveries autonomously.

### Knowledge Recording Rules

Add these rules to your project's `CLAUDE.md` to control how Claude Code interacts with the knowledge base:

```markdown
## Knowledge Recording
- Aggregate knowledge in `.claude/knowledge/entries/` as one file per entry (with YAML frontmatter)
- Entries are mutable — edit in place, rely on git for history
- See `/record-knowledge` skill for format and consolidation procedures
```

### Knowledge Base Lookup

Add a lookup section so Claude Code searches for relevant entries at the start of each task. You can adjust the strategy based on expected result volume:

```markdown
## Knowledge Base Lookup

Before starting work, search for relevant active knowledge entries.
Use the Explore subagent for searches that may hit multiple entries (keeps main context clean).

### Search (multiple entries expected)

Delegate to Explore subagent:
- "Search `.claude/knowledge/entries/` for active entries related to `<keyword>` and summarize relevant findings"
- The subagent reads entries and returns only a concise summary to the main context

### Search (single entry expected)

Read directly — subagent overhead is unnecessary:
\```bash
rg '^status: active' .claude/knowledge/entries/ -l | xargs rg '<keyword>' -l
\```
Then Read the matching file.

### Rules
- Only reference entries with `status: active` — ignore `deprecated` entries
- Replace `<keyword>` with terms relevant to the current task (service name, technology, etc.)
```

### Plan Persistence

Add these rules so Claude Code maintains plans across sessions:

```markdown
## Plan Persistence
- Save plans and work status as files in `.claude/tasks/` (see `.claude/tasks/CLAUDE.md` for details)
- At session start, check `.claude/tasks/readme.md` for incomplete plans before starting work
```

### Workflow Integration

You can reference both systems from other workflow rules in `CLAUDE.md`. For example:

```markdown
## Workflow Rules
- Start with plan mode for tasks with 3+ steps
- Add rules to CLAUDE.md when the user points out a recurring mistake

## Progress Update
When the user says "update progress", execute all of the following:
1. Update `.claude/tasks/` todo.md and readme.md
2. Record any knowledge gained during work
3. Commit and push changes
```

This way, Claude Code naturally manages plans and records discoveries as part of its normal workflow — no manual intervention required.

## Why Use It in a Git Repository

This knowledge base is designed to live inside a Git repository (GitHub, GitLab, etc.) and be committed alongside your code. This brings several benefits:

### Share tacit knowledge with your team

Knowledge entries are plain Markdown files in the repository. Every team member — and every Claude Code session they start — can access the same accumulated knowledge. Pitfalls one person discovers on Monday are available to the entire team on Tuesday, without anyone needing to remember to share them.

This is especially valuable for:
- **Onboarding**: New members inherit the project's accumulated wisdom from day one
- **Async collaboration**: Discoveries made in one session are preserved for others working in different time zones or schedules
- **Knowledge retention**: When team members rotate off a project, their discoveries remain in the repository

### Survive across Claude Code sessions

Claude Code starts each session with a fresh context. Without persistent storage, every discovery — environment quirks, failed approaches, configuration gotchas — is lost when the session ends. The knowledge base solves this: entries written in one session are automatically available in the next, giving Claude Code a form of long-term memory anchored to the project.

### Stay in sync with the code

Because entries live in the same repository as the code, they follow the same branch/merge/review workflow. Knowledge stays versioned alongside the code it describes. When code changes make an entry obsolete, the proximity makes it easy to notice and update.

### Browsable in your Git hosting platform's web UI

Entries use Markdown with relative links, so they render cleanly in GitHub and GitLab's web interface. Team members can browse, search, and review knowledge without any special tooling — just a web browser.

## Customization

### Tags

Edit `.claude/knowledge/CLAUDE.md` to maintain a tag registry for your project. Tags use lowercase kebab-case with `#` prefix as a flat list:

```
`#docker` `#postgresql` `#react` `#pitfall` `#workaround` `#design-practice`
```

Claude Code checks the registry before creating new tags to avoid duplicates.

### Entry Author

The `author` field in entries defaults to `@<username>`. Set this to match your Git hosting platform username.

### Integration with Project Workflow

You can extend the skill by editing `SKILL.md` to fit your workflow — for example, adding project-specific tag categories or linking to your issue tracker.

## License

MIT
