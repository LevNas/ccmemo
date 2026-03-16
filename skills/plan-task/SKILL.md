---
name: plan-task
description: >-
  Persist multi-step plans and task progress across Claude Code sessions. Use when starting work
  that may span multiple sessions, resuming incomplete plans, or updating task progress. Supports
  two modes: Git-tracked (shared via commits) and Issue-centric (issue tracker as primary source
  of truth, local scratchpad for sessions).
license: MIT
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---

# Plan & Task Persistence

## Goal

Maintain plans and task progress across Claude Code sessions so that work can be resumed without losing context.

## Session Start (New Sessions Only)

On new session start (not resume), check for incomplete work:

- **Git-tracked mode**: Read `.claude/tasks/readme.md` for incomplete plans
- **Issue-centric mode**: Check assigned issues (e.g., `gh issue list --assignee=@me`) for open tasks

On progress update, also update the issue tracker (comments, checklists) if applicable.

## Progress Update Triggers

- **Explicit**: User says "progress update" or equivalent → execute immediately
- **Implicit**: User signals a break or completion (e.g., "thanks", "taking a break", "that's it for today") → suggest "Shall I update progress?" before proceeding
- **Milestone**: A significant chunk of work is completed → suggest "Shall I update progress?"

Progress update procedure:
1. Check related issues (`gh issue list`)
2. Post a progress comment (completed work, next steps, related work)
3. Update issue checklists if applicable
4. Commit and push if there are pending changes

## Setup

Copy the template files to `.claude/tasks/`:

```bash
mkdir -p .claude/tasks
cp assets/tasks-CLAUDE.md .claude/tasks/CLAUDE.md
cp assets/tasks-readme.md .claude/tasks/readme.md
```

For **Issue-centric mode**, also add `.claude/tasks/` to `.gitignore`.

## Two Modes

Choose the mode that fits your team's workflow:

| | Git-tracked mode | Issue-centric mode |
|---|---|---|
| Primary source of truth | `.claude/tasks/` (committed to Git) | Issue tracker (GitLab, GitHub, Jira, etc.) |
| `.claude/tasks/` role | Shared plan/task storage | Local working memo (gitignored) |
| Team visibility | Via Git commits | Via issue tracker |
| Best for | Small teams, single-repo projects | Teams already using an issue tracker |

### Issue-centric mode

When an issue tracker is the primary source of truth:

- `.claude/tasks/` is **gitignored** — add `.claude/tasks/` to `.gitignore`
- Plans and progress live in the issue tracker; `.claude/tasks/` is a local scratchpad for the current session
- Anything worth sharing with the team belongs in the issue tracker, not in `.claude/tasks/`
- At session start, check assigned issues in your tracker instead of `.claude/tasks/readme.md`

The rest of this document describes **Git-tracked mode**. For issue-centric mode, adapt the procedures below: use `.claude/tasks/` as a local memo and post shared artifacts to the issue tracker.

---

## Git-tracked mode

## When to Use

- Starting a multi-step task that may span multiple sessions
- Resuming work — check `.claude/tasks/readme.md` for incomplete plans
- Updating progress on an existing plan
- Closing out or archiving a completed plan

## Directory Naming

```
.claude/tasks/<slug>-<account>-<date>/           # Without issue reference
.claude/tasks/<slug>-i<issue>-<account>-<date>/  # With issue reference
```

- Separate components with `-` (hyphen)
- Separate words within slug with `_` (underscore)
- Slug uses lowercase alphanumeric only; date is YYYYMMDD
- Example: `docker_migration-alice-20260304/`
- Example: `auth_refactor-i42-bob-20260304/`

## Directory Structure

```
.claude/tasks/
├── readme.md                  # Index of all plans (status and summary)
├── <slug>-<account>-<date>/
│   ├── readme.md              # Purpose, current state, file listing
│   ├── plan-v1.md             # Initial plan (approach, design decisions, context)
│   ├── plan-v2.md             # Revised plan (v1 remains unchanged)
│   ├── todo.md                # Current task progress (frequently updated)
│   └── ...
```

## Creating a Plan

1. Create `.claude/tasks/<slug>-<account>-<date>/` following the naming convention
2. **Search related knowledge**: Grep `.claude/knowledge/entries/` for tags and keywords related to the plan's topic. Look for:
   - Past pitfalls (`#pitfall`) that may recur
   - Design decisions and their rationale
   - Related tooling or configuration knowledge
3. Write `plan-v1.md` with: approach, design decisions, background, completion criteria. If related knowledge was found in step 2, include a **Related Knowledge** section:
   ```markdown
   ## Related Knowledge
   - [entry title](../../knowledge/entries/slug.md) — why it's relevant
   ```
4. Write `todo.md` with a checkbox task list
5. Write `readme.md` with the plan's purpose and current state
6. Add an entry to `.claude/tasks/readme.md`

## Working on Tasks

- Update `todo.md` only — do not modify plan files
- Mark task status: `- [ ]` (pending) → `- [~]` (in progress) → `- [x]` (done)
- Record discovered issues or blockers indented below the relevant task
- Add new task lines to `todo.md` as work expands
- **Issue sync**: When updating `todo.md` or `readme.md`, also update the linked issue (if any) with the same progress. This is not optional — if an issue link exists, keep it in sync

### Issue Tracker Sync

If the plan is linked to an issue in your project's issue tracker:

- Check the issue for updates before starting work on `.claude/tasks/`
- Reflect any direction changes or new comments into `.claude/tasks/`
- Include the issue reference in commit messages
- Update the issue body or post a progress comment when task status changes

## Revising a Plan

- Do NOT edit existing `plan-vN.md` files — create `plan-vN+1.md` instead
- Reset `todo.md` to match the new plan (carry over incomplete items)
- Update `<slug>/readme.md` with which version is current and why the revision was needed
- Claude Code reads only the latest `plan-vN.md` and `todo.md`

## Committing Progress

- Commit when task progress changes (completion, blockers found, etc.)
- If linked to an issue, report progress there as well

## Pausing or Completing Work

### On session end or interruption

- Update `<slug>/readme.md` with current state and handoff notes
- Next session starts by checking `.claude/tasks/readme.md` for incomplete plans

### On plan completion

- Mark all tasks in `todo.md` as done
- Update `<slug>/readme.md` state to "completed"
- Move the entry in `.claude/tasks/readme.md` from the active table to the completed table
- **Knowledge extraction**: Review the completed work and extract lessons learned:
  1. Scan `todo.md` for blockers, workarounds, and unexpected discoveries noted during execution
  2. Compare the plan (what was expected) with the actual outcome (what happened)
  3. For each piece of tacit knowledge found, invoke `record-knowledge` to create an entry
  4. If related knowledge entries were referenced in the plan, update them with new findings
- **Retrospective prompt**: Notify the user with a brief summary:
  - What was completed
  - What knowledge was extracted
  - Ask: "Are there lessons from this work not yet captured?"
