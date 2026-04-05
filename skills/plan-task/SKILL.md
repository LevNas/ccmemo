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
2. Prepare structured input fields for the subagent
3. Spawn a subagent with the following configuration:

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
- `{plugin_root}` — the plugin's installation path (shown in the skill loading message as "Base directory for this skill")
- `{project_root}` — the project working directory

4. Report the subagent's result to the user (operation performed, files modified, current status)
5. If the subagent returns knowledge candidates (typically from `complete` mode), invoke `record-knowledge` for each candidate using the structured fields provided

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
