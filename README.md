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

Claude Code uses `/plan-task` to persist multi-step plans across sessions. Two modes are available:

#### Git-tracked mode (default)

`.claude/tasks/` is committed to Git and serves as the shared source of truth:

1. **Create**: Start a plan with `plan-v1.md`, `todo.md`, and `readme.md` in `.claude/tasks/<slug>-<account>-<date>/`
2. **Work**: Update `todo.md` as tasks progress (`[ ]` → `[~]` → `[x]`)
3. **Revise**: If the plan changes, create `plan-v2.md` (previous versions are preserved)
4. **Pause/Resume**: On session end, update the plan's `readme.md` with handoff notes. Next session checks `.claude/tasks/readme.md` for incomplete plans
5. **Complete**: Mark done in both the plan directory and the task index

If your project uses an issue tracker, plans can optionally link to issues for bidirectional progress tracking.

#### Issue-centric mode

For teams already using an issue tracker (GitLab, GitHub Issues, Jira, etc.) as the primary source of truth:

1. **Gitignore tasks**: Add `.claude/tasks/` to `.gitignore`
2. **Use issue tracker**: Plans, progress, and decisions live in the issue tracker
3. **Local scratchpad**: `.claude/tasks/` becomes a local working memo for the current session
4. **Session start**: Check assigned issues (e.g., `glab issue list --assignee=@me`) instead of `.claude/tasks/readme.md`

See the [CLAUDE.md sample](#issue-centric-plan-persistence) below for configuration examples.

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

Below is a practical example of how to configure your project's `CLAUDE.md` to integrate ccmemo into Claude Code's workflow. Adapt the sections you need.

### Thinking Partner Declaration

Claude Code is not just a coding assistant — it can also serve as a thinking partner. Declare this role explicitly:

```markdown
# Project Harness

Claude Code is not just a coding assistant — it also serves as a "thinking partner."
```

### What to Optimize For

```markdown
## What to optimize for
- Reproducibility over cleverness
- Small diffs, fast feedback loops
- Simplicity — but never at the cost of usability
```

### Response Quality

Help Claude Code structure ambiguous input and be transparent about uncertainty:

```markdown
## Response Quality
- Perform a 3-level self-review before responding (broad → mid → narrow perspective)
- If any command or code hasn't been verified, disclose that before presenting it
- **Structuring**: When the user's message is ambiguous, restate it as a structured summary
  (bullet points, indentation, markdown) at the top of the response before proceeding
- **Uncertainty flow** (in order):
  1. Search `.claude/knowledge/entries/` for relevant active entries
  2. If not found, offer a clarifying question to the user rather than guessing
  3. If a question doesn't apply, respond with "No knowledge entry found —
     the following includes inference/speculation"
```

### Knowledge Recording Rules

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

### Plan Persistence (Git-tracked mode)

For projects where `.claude/tasks/` is committed to Git:

```markdown
## Plan Persistence
- Save plans and work status as files in `.claude/tasks/` (see `.claude/tasks/CLAUDE.md` for details)
- At session start, check `.claude/tasks/readme.md` for incomplete plans before starting work
```

### <a id="issue-centric-plan-persistence"></a>Plan Persistence (Issue-centric mode)

For projects using an issue tracker as the primary source of truth:

```markdown
## Plan Persistence
- **Primary**: Issue tracker is the single source of truth for plans and progress
  - Create plan issues using a plan template
  - Track progress via checklist updates + comments
- **Secondary**: `.claude/tasks/` is a local working memo (gitignored)
  - Persists across Claude Code sessions on the same machine
  - Not shared with other members — anything worth sharing belongs in the issue tracker
- **Session Start**:
  1. Check assigned issues in your tracker (e.g., `glab issue list --assignee=@me`)
  2. Read the target issue's comments to understand the latest state
```

### Issue Management

For teams using issue-centric mode, add quality checks and stalled issue diagnosis:

```markdown
## Issue Management
- Issue quality checks — run when creating, reading, or updating issues:
  - Granularity: is the scope small enough for one person to complete in a reasonable timeframe?
  - Priority: does it have a priority label? Is the priority justified?
  - Risk: are risks and blockers identified?
- Stalled issue diagnosis: scan on-hold issues for stalled patterns at session start
- Details can be separated into knowledge entries to keep CLAUDE.md concise
```

### Progress Update

#### Git-tracked mode

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Update `.claude/tasks/` todo.md and readme.md
2. Record any knowledge gained during work
3. Commit and push changes
```

#### Issue-centric mode

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Post a progress comment on the issue (latest status, completed items, next actions)
2. Update issue labels, milestones, and checklists
3. Update related document execution records and changelogs
4. Commit and push changes
5. Record any knowledge gained during work to `.claude/knowledge/entries/`
```

### Agent Behavior

Delegate non-primary work to subagents to keep the main session context lean:

```markdown
## Agent Behavior
- Delegate non-primary work to subagents to keep the main session context lean
  - Research, multi-file searches, knowledge lookups → Explore subagent
  - Only pull the summary back into the main context, not raw results
```

### Language

Separate the language used for config/data files from the language used with users:

```markdown
## Language
- Claude Code config/data (CLAUDE.md, `.claude/tasks/`): any language (English recommended)
- Respond to the user in their preferred language
- Knowledge entries: user's preferred language
- Issue tracker: team's common language
```

This way, Claude Code naturally manages plans and records discoveries as part of its normal workflow — no manual intervention required.

## Why Use It in a Git Repository

Both the knowledge base and plan persistence are designed to live inside a Git repository (GitHub, GitLab, etc.) and be committed alongside your code. This brings several benefits:

### Share context with your team

Knowledge entries and plans are plain Markdown files in the repository. Every team member — and every Claude Code session they start — can access the same accumulated knowledge and see the status of ongoing work. Pitfalls one person discovers on Monday are available to the entire team on Tuesday. A plan started by one member can be resumed by another.

This is especially valuable for:
- **Onboarding**: New members inherit the project's accumulated wisdom and can see current work plans from day one
- **Async collaboration**: Discoveries and plans made in one session are preserved for others working in different time zones or schedules
- **Knowledge retention**: When team members rotate off a project, their discoveries and work context remain in the repository

### Survive across Claude Code sessions

Claude Code starts each session with a fresh context. Without persistent storage, every discovery — environment quirks, failed approaches, configuration gotchas — and every work plan is lost when the session ends. ccmemo solves this: knowledge entries and task progress written in one session are automatically available in the next, giving Claude Code a form of long-term memory anchored to the project.

### Stay in sync with the code

Because everything lives in the same repository as the code, it follows the same branch/merge/review workflow. Knowledge and plans stay versioned alongside the code they describe. When code changes make an entry or plan obsolete, the proximity makes it easy to notice and update.

### Browsable in your Git hosting platform's web UI

All files use Markdown with relative links, so they render cleanly in GitHub and GitLab's web interface. Team members can browse, search, and review knowledge entries and plans without any special tooling — just a web browser.

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

## License

MIT
