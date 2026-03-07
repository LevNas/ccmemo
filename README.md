# ccmemo

Claude Code starts every session with a blank slate. The quirk you debugged yesterday, the plan you were halfway through — all gone when the session ends. ccmemo saves that knowledge and progress as Markdown files in your repository, so the next session picks up where you left off.

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

## License

MIT
