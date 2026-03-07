# CLAUDE.md Configuration Examples

Practical examples of how to configure your project's `CLAUDE.md` to integrate ccmemo into Claude Code's workflow. Adapt the sections you need.

## Thinking Partner Declaration

Claude Code is not just a coding assistant — it can also serve as a thinking partner. Declare this role explicitly:

```markdown
# Project Harness

Claude Code is not just a coding assistant — it also serves as a "thinking partner."
```

## What to Optimize For

```markdown
## What to optimize for
- Reproducibility over cleverness
- Small diffs, fast feedback loops
- Simplicity — but never at the cost of usability
```

## Response Quality

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

## Knowledge Recording Rules

```markdown
## Knowledge Recording
- Aggregate knowledge in `.claude/knowledge/entries/` as one file per entry (with YAML frontmatter)
- Entries are mutable — edit in place, rely on git for history
- See `/record-knowledge` skill for format and consolidation procedures
```

## Knowledge Base Lookup

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

## Plan Persistence (Git-tracked mode)

For projects where `.claude/tasks/` is committed to Git:

```markdown
## Plan Persistence
- Save plans and work status as files in `.claude/tasks/` (see `.claude/tasks/CLAUDE.md` for details)
- At session start, check `.claude/tasks/readme.md` for incomplete plans before starting work
```

## Plan Persistence (Issue-centric mode)

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

## Issue Management

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

## Progress Update

### Git-tracked mode

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Update `.claude/tasks/` todo.md and readme.md
2. Record any knowledge gained during work
3. Commit and push changes
```

### Issue-centric mode

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Post a progress comment on the issue (latest status, completed items, next actions)
2. Update issue labels, milestones, and checklists
3. Update related document execution records and changelogs
4. Commit and push changes
5. Record any knowledge gained during work to `.claude/knowledge/entries/`
```

## Agent Behavior

Delegate non-primary work to subagents to keep the main session context lean:

```markdown
## Agent Behavior
- Delegate non-primary work to subagents to keep the main session context lean
  - Research, multi-file searches, knowledge lookups → Explore subagent
  - Only pull the summary back into the main context, not raw results
```

## Language

Separate the language used for config/data files from the language used with users:

```markdown
## Language
- Claude Code config/data (CLAUDE.md, `.claude/tasks/`): any language (English recommended)
- Respond to the user in their preferred language
- Knowledge entries: user's preferred language
- Issue tracker: team's common language
```
