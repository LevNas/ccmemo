---
name: plan-task
description: >-
  Persist multi-step plans and task progress across Claude Code sessions. Use when starting work
  that may span multiple sessions, resuming incomplete plans, or updating task progress. Supports
  two modes: Git-tracked (shared via commits) and Issue-centric (issue tracker as primary source
  of truth, local scratchpad for sessions).
license: MIT
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Agent
---

# Plan & Task Persistence

## Goal

Maintain plans and task progress across Claude Code sessions so that work can be resumed without losing context.

## Execution

Delegate the plan/task work to a Sonnet subagent to minimize main context consumption.

1. Determine the operation mode from user input or session context
2. **For `create-plan` and `revise-plan`, settle with the user first.** The plan written in these modes is a spec: a record of decisions already made, which later sessions build from. The subagent cannot talk to the user, so do these in the main session before spawning it:
   - If a decision the plan needs is still open, settle it with the user now (with ccharness installed, `ccharness:settle-decisions`). Do not leave it for the subagent to fill in.
   - Show the user how the work will be verified (Verification) and get their agreement before the plan is written. Prefer checks that already exist (tests, commands) and as few as will do.
   - Collect what was considered and left out, with the reason (Out of Scope). If the conversation has none, ask the user; "none" is not an answer.
   - For `revise-plan`: the Out of Scope items are the ones of the current plan that still hold plus what the revision leaves out. If the current plan has no Out of Scope section and the conversation has none, ask the user. Agree the Verification again only if the revision changes it; otherwise pass the current one.
   - Put the agreed Verification and Out of Scope in the `settled` field below.
3. Prepare structured input fields for the subagent
4. Spawn a subagent with the following configuration:

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  description: "plan and task management",
  prompt: |
    You are a plan and task management agent. Read the procedure file and follow it precisely.

    ## Operation
    {mode}

    ## Input
    ### what（事実）
    {what}

    ### why（判断理由）
    {why}

    ### context（背景情報）
    {context}

    ### tags_hint（推奨タグ）
    {tags_hint}

    ### settled（create-plan / revise-plan: agreed Verification and Out of Scope）
    {settled}

    ## Instructions
    1. Read the procedure file at: {plugin_root}/skills/plan-task/procedure.md
    2. Execute the procedure section matching the operation mode: {mode}
    3. The project root is: {project_root}
    4. The tasks directory is: {project_root}/.claude/tasks/
    5. For setup, asset files are at: {plugin_root}/skills/plan-task/assets/
    6. Return: operation performed, files created/modified, current plan status, and knowledge candidates (if any)
)
```

Replace the placeholders:
- `{mode}` — one of: session-start, create-plan, update-progress, revise-plan, pause, complete
- `{what}` — the factual situation (e.g., "user wants to create a plan for Docker migration" or "three tasks completed, one blocker found")
- `{why}` — why this operation is needed now (e.g., "new session started, need to check for incomplete work")
- `{context}` — relevant background (current branch, related issues, recently modified files, active plan slug if known)
- `{tags_hint}` — recommended tags for any knowledge entries that may be created (especially relevant for `complete` mode)
- `{settled}` — for `create-plan` and `revise-plan`: the Verification the user agreed to and the Out of Scope items, from step 2; for other modes, "n/a"
- `{plugin_root}` — the plugin's installation path (shown in the skill loading message as "Base directory for this skill")
- `{project_root}` — the project working directory

5. Report the subagent's result to the user (operation performed, files modified, current status). If a `create-plan` or `revise-plan` subagent returns what is missing instead of writing the plan, settle that with the user as in step 2 and spawn it again.
6. If the subagent returns knowledge candidates (typically from `complete` mode), invoke `record-knowledge` for each candidate using the structured fields provided

IMPORTANT: The procedure file path uses the plugin's base directory, NOT the project directory. Read the "Base directory for this skill" line from the skill loading message to determine the correct path.

### Mode Selection Guide

| User signal | Mode |
|---|---|
| Session just started / post-compaction / resume | `session-start` |
| "plan ...", "let's plan ...", "create a plan for ..." | `create-plan` |
| "progress update", "update progress", signals a break | `update-progress` |
| "revise the plan", "the approach needs to change" | `revise-plan` |
| "taking a break", "that's it for today", "pausing" | `pause` |
| "plan is done", "all tasks complete", "wrap up" | `complete` |
